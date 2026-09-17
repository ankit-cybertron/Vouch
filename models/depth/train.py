"""
Vouch — Model 2: Review Depth Scorer Training.

Fine-tunes DistilBERT on review comments classified into 6 depth classes.

Depth classes and their weights:
    0 — Rubber-stamp     (0.00) — "LGTM", bare approval
    1 — Nit / style      (0.15) — naming, formatting
    2 — Clarifying       (0.45) — "why is this retried 3 times?"
    3 — Logic concern    (0.80) — "this is off-by-one when list is empty"
    4 — Architecture     (0.90) — "this couples scheduler to storage"
    5 — Security concern (1.00) — "reachable with unvalidated token"

Usage:
    python train.py --comments ./data/comments_labelled.parquet \
                    --output-dir ./output/depth_model
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from torch.utils.data import Dataset, DataLoader
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    EarlyStoppingCallback,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODEL_NAME = "distilbert-base-uncased"  # fallback: "sentence-transformers/all-MiniLM-L6-v2"
NUM_LABELS = 6

DEPTH_WEIGHTS = [0.0, 0.15, 0.45, 0.80, 0.90, 1.00]
LABEL_NAMES = [
    "rubber_stamp", "nit_style", "clarifying",
    "logic_concern", "architecture", "security",
]


class ReviewCommentDataset(Dataset):
    def __init__(self, texts: list[str], labels: list[int], tokenizer, max_length: int = 256):
        self.encodings = tokenizer(
            texts,
            truncation=True,
            padding=True,
            max_length=max_length,
            return_tensors="pt",
        )
        self.labels = torch.tensor(labels, dtype=torch.long)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return {k: v[idx] for k, v in self.encodings.items()} | {"labels": self.labels[idx]}


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    f1 = f1_score(labels, preds, average="weighted", zero_division=0)
    return {"f1_weighted": f1}


def train(
    comments_parquet: str,
    output_dir: str,
    epochs: int = 3,
    batch_size: int = 16,
    lr: float = 2e-5,
) -> dict:
    df = pd.read_parquet(comments_parquet)

    # Expect columns: body, depth_label (int 0-5), verified (bool)
    required_cols = {"body", "depth_label"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in parquet: {missing}")

    texts = df["body"].fillna("").tolist()
    labels = df["depth_label"].astype(int).tolist()

    # Stratified split — keep class distribution consistent
    train_texts, val_texts, train_labels, val_labels = train_test_split(
        texts, labels, test_size=0.15, stratify=labels, random_state=42
    )

    logger.info(
        "Training: %d samples | Validation: %d samples",
        len(train_texts), len(val_texts),
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=NUM_LABELS)

    train_dataset = ReviewCommentDataset(train_texts, train_labels, tokenizer)
    val_dataset = ReviewCommentDataset(val_texts, val_labels, tokenizer)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(out_path / "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=lr,
        weight_decay=0.01,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="f1_weighted",
        logging_dir=str(out_path / "logs"),
        logging_steps=50,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    )

    trainer.train()

    # Final evaluation
    preds_output = trainer.predict(val_dataset)
    preds = np.argmax(preds_output.predictions, axis=-1)
    report = classification_report(val_labels, preds, target_names=LABEL_NAMES, output_dict=True)
    logger.info("\n%s", classification_report(val_labels, preds, target_names=LABEL_NAMES))

    # Save model and tokenizer
    model.save_pretrained(str(out_path / "model"))
    tokenizer.save_pretrained(str(out_path / "model"))

    meta = {
        "base_model": MODEL_NAME,
        "num_labels": NUM_LABELS,
        "label_names": LABEL_NAMES,
        "depth_weights": DEPTH_WEIGHTS,
        "val_size": len(val_texts),
        "classification_report": report,
        "note": "Verify stratified sample by hand before reporting precision figures.",
    }
    (out_path / "meta.json").write_text(json.dumps(meta, indent=2))
    logger.info("Model saved to %s", out_path / "model")
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fine-tune depth scorer")
    parser.add_argument("--comments", required=True, help="Labelled comments parquet")
    parser.add_argument("--output-dir", default="./output/depth_model")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    args = parser.parse_args()
    train(args.comments, args.output_dir, args.epochs, args.batch_size, args.lr)
