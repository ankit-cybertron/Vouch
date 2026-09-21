#!/usr/bin/env python3
"""
Vouch — SageMaker Model Packaging & Endpoint Deployment Script.

Packages Model 1 (Change Risk XGBoost) and Model 2 (Review Depth Transformer)
into SageMaker-compatible model.tar.gz bundles and deploys/updates live endpoints.

Usage:
    python scripts/deploy_sagemaker.py --package-only
    python scripts/deploy_sagemaker.py --bucket my-bucket --role-arn arn:aws:iam::...
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

try:
    from botocore.exceptions import ClientError as BotocoreClientError
except ImportError:
    BotocoreClientError = Exception  # fallback for local runs without boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sagemaker_deploy")

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"

RISK_ENDPOINT_DEFAULT = os.environ.get("RISK_MODEL_ENDPOINT", "vouch-risk-model-prod")
DEPTH_ENDPOINT_DEFAULT = os.environ.get("DEPTH_MODEL_ENDPOINT", "vouch-depth-model-prod")
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"))


def package_risk_model(output_dir: Path) -> Path:
    """Package Model 1 (Risk XGBoost) into model.tar.gz."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tar_path = output_dir / "risk_model.tar.gz"
    risk_dir = ROOT_DIR / "models" / "risk"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Copy inference handler
        code_dir = tmp_path / "code"
        code_dir.mkdir(parents=True, exist_ok=True)

        inf_file = risk_dir / "inference.py"
        if inf_file.exists():
            (code_dir / "inference.py").write_text(inf_file.read_text())

        # Include model weights if present or write base schema model
        model_file = risk_dir / "risk_model.json"
        if model_file.exists():
            (tmp_path / "risk_model.json").write_text(model_file.read_text())
        else:
            # Baseline placeholder model structure if un-trained in repo
            (tmp_path / "risk_model.json").write_text('{"version": "1.0", "type": "xgboost"}')

        with tarfile.open(tar_path, "w:gz") as tar:
            for item in tmp_path.iterdir():
                tar.add(item, arcname=item.name)

    logger.info("Packaged Risk Model artifact: %s (%.2f KB)", tar_path, tar_path.stat().st_size / 1024)
    return tar_path


def package_depth_model(output_dir: Path) -> Path:
    """Package Model 2 (Depth Transformer) into model.tar.gz."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tar_path = output_dir / "depth_model.tar.gz"
    depth_dir = ROOT_DIR / "models" / "depth"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        code_dir = tmp_path / "code"
        code_dir.mkdir(parents=True, exist_ok=True)

        inf_file = depth_dir / "inference.py"
        if inf_file.exists():
            (code_dir / "inference.py").write_text(inf_file.read_text())

        model_dir = depth_dir / "model"
        if model_dir.is_dir():
            import shutil
            shutil.copytree(model_dir, tmp_path / "model")
        else:
            (tmp_path / "model").mkdir()
            (tmp_path / "model" / "config.json").write_text('{"model_type": "distilbert"}')

        with tarfile.open(tar_path, "w:gz") as tar:
            for item in tmp_path.iterdir():
                tar.add(item, arcname=item.name)

    logger.info("Packaged Depth Model artifact: %s (%.2f KB)", tar_path, tar_path.stat().st_size / 1024)
    return tar_path


def deploy_endpoint(
    sm_client,
    s3_client,
    model_name: str,
    endpoint_name: str,
    tar_path: Path,
    s3_bucket: str,
    s3_prefix: str,
    role_arn: str,
    image_uri: str,
    instance_type: str = "ml.m5.large",
) -> None:
    """Upload artifact, register model, create/update endpoint, and wait for InService."""
    s3_key = f"{s3_prefix}/{model_name}/{tar_path.name}"
    logger.info("Uploading %s to s3://%s/%s ...", tar_path.name, s3_bucket, s3_key)
    s3_client.upload_file(str(tar_path), s3_bucket, s3_key)
    s3_model_data = f"s3://{s3_bucket}/{s3_key}"

    timestamp = int(time.time())
    unique_model_name = f"{model_name}-{timestamp}"
    unique_config_name = f"{endpoint_name}-config-{timestamp}"

    logger.info("Creating SageMaker model: %s", unique_model_name)
    logger.info("  Image URI:      %s", image_uri)
    logger.info("  Model data:     %s", s3_model_data)
    logger.info("  Execution role: %s", role_arn)
    try:
        sm_client.create_model(
            ModelName=unique_model_name,
            PrimaryContainer={
                "Image": image_uri,
                "ModelDataUrl": s3_model_data,
                # SAGEMAKER_PROGRAM tells the container which script to run.
                # Do NOT set SAGEMAKER_SUBMIT_DIRECTORY — pointing it at the
                # model tar (or any path) triggers unnecessary ECR layer
                # validations. The container locates code/inference.py inside
                # the model tar automatically when SAGEMAKER_PROGRAM is set.
                "Environment": {
                    "SAGEMAKER_PROGRAM": "inference.py",
                },
            },
            ExecutionRoleArn=role_arn,
        )
    except Exception as exc:
        logger.error(
            "CreateModel failed for %s\n"
            "  Image URI:      %s\n"
            "  Model data:     %s\n"
            "  Execution role: %s\n"
            "  Error: %s",
            unique_model_name, image_uri, s3_model_data, role_arn, exc,
        )
        raise

    logger.info("Creating SageMaker endpoint configuration: %s", unique_config_name)
    sm_client.create_endpoint_config(
        EndpointConfigName=unique_config_name,
        ProductionVariants=[
            {
                "VariantName": "AllTraffic",
                "ModelName": unique_model_name,
                "InitialInstanceCount": 1,
                "InstanceType": instance_type,
                "InitialVariantWeight": 1.0,
            }
        ],
    )

    # Check if endpoint already exists
    existing = False
    try:
        sm_client.describe_endpoint(EndpointName=endpoint_name)
        existing = True
    except BotocoreClientError as exc:
        msg = exc.response.get("Error", {}).get("Message", "").lower()
        if "does not exist" in msg:
            existing = False
        else:
            raise
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            existing = False
        else:
            raise

    if existing:
        logger.info("Updating existing SageMaker endpoint: %s", endpoint_name)
        sm_client.update_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=unique_config_name,
        )
    else:
        logger.info("Creating new SageMaker endpoint: %s", endpoint_name)
        sm_client.create_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=unique_config_name,
        )

    logger.info("Waiting for SageMaker endpoint '%s' to become InService...", endpoint_name)
    waiter = sm_client.get_waiter("endpoint_in_service")
    waiter.wait(
        EndpointName=endpoint_name,
        WaiterConfig={"Delay": 30, "MaxAttempts": 30},
    )

    desc = sm_client.describe_endpoint(EndpointName=endpoint_name)
    status = desc.get("EndpointStatus")
    if status != "InService":
        failure_reason = desc.get("FailureReason", "Unknown failure reason")
        raise RuntimeError(f"SageMaker endpoint {endpoint_name} failed with status {status}: {failure_reason}")

    logger.info("SageMaker endpoint '%s' is InService!", endpoint_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package and deploy Vouch SageMaker models.")
    parser.add_argument("--package-only", action="store_true", help="Only package model artifacts into dist/")
    parser.add_argument("--bucket", type=str, default=os.environ.get("SAGEMAKER_BUCKET", ""), help="S3 bucket for artifacts")
    parser.add_argument("--role-arn", type=str, default=os.environ.get("SAGEMAKER_ROLE_ARN", ""), help="IAM role ARN for SageMaker")
    parser.add_argument("--region", type=str, default=AWS_REGION, help="AWS region")
    parser.add_argument("--risk-endpoint", type=str, default=RISK_ENDPOINT_DEFAULT, help="Endpoint name for risk model")
    parser.add_argument("--depth-endpoint", type=str, default=DEPTH_ENDPOINT_DEFAULT, help="Endpoint name for depth model")
    parser.add_argument("--ecr-account-id", type=str, default=os.environ.get("ECR_ACCOUNT_ID", ""), help="AWS Account ID for private ECR")
    args = parser.parse_args()

    # Startup validation: ECR_ACCOUNT_ID is required for SageMaker deployment
    ecr_account_id = (args.ecr_account_id or os.environ.get("ECR_ACCOUNT_ID", "")).strip()
    if not args.package_only and not ecr_account_id:
        logger.error("ECR_ACCOUNT_ID is required for SageMaker deployment")
        sys.exit("ECR_ACCOUNT_ID is required for SageMaker deployment")

    # Construct private ECR registry and repository image URIs
    registry = f"{ecr_account_id}.dkr.ecr.{args.region}.amazonaws.com" if ecr_account_id else ""
    xgb_tag = os.environ.get("XGB_IMAGE_TAG", "3.0-5")
    pytorch_tag = os.environ.get("PYTORCH_IMAGE_TAG", "2.3.0-cpu-py311")

    xgb_image = f"{registry}/vouch/sagemaker-xgboost:{xgb_tag}" if registry else ""
    pytorch_image = f"{registry}/vouch/pytorch-inference:{pytorch_tag}" if registry else ""

    if ecr_account_id:
        if "763104351884" in xgb_image or "763104351884" in pytorch_image:
            raise ValueError("Image URI must not contain AWS DLC account 763104351884. Use private ECR instead.")
        logger.info("Private ECR registry: %s", registry)
        logger.info("XGBoost image:  %s", xgb_image)
        logger.info("PyTorch image:  %s", pytorch_image)

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    risk_tar = package_risk_model(DIST_DIR)
    depth_tar = package_depth_model(DIST_DIR)

    if args.package_only:
        logger.info("Model packaging completed successfully.")
        return

    if not args.bucket or not args.role_arn:
        logger.warning(
            "Missing SAGEMAKER_BUCKET or SAGEMAKER_ROLE_ARN environment variables. "
            "Packaged artifacts verified at %s and %s. Skipping live AWS API calls.",
            risk_tar, depth_tar
        )
        return

    # Guard: skip live deployment if only placeholder artifacts are present
    has_risk = (ROOT_DIR / "models" / "risk" / "risk_model.json").exists() and \
                (ROOT_DIR / "models" / "risk" / "risk_model.json").stat().st_size > 200
    has_depth = (ROOT_DIR / "models" / "depth" / "model").is_dir() and \
                any((ROOT_DIR / "models" / "depth" / "model").iterdir())

    if not (has_risk or has_depth):
        logger.warning(
            "No trained model artifacts found; skipping endpoint deployment. "
            "Run 'python scripts/deploy_sagemaker.py --package-only' to package only."
        )
        return

    import boto3
    s3_client = boto3.client("s3", region_name=args.region)
    sm_client = boto3.client("sagemaker", region_name=args.region)

    deploy_endpoint(
        sm_client=sm_client,
        s3_client=s3_client,
        model_name="vouch-risk-model",
        endpoint_name=args.risk_endpoint,
        tar_path=risk_tar,
        s3_bucket=args.bucket,
        s3_prefix="models",
        role_arn=args.role_arn,
        image_uri=xgb_image,
        instance_type="ml.m5.large",
    )

    deploy_endpoint(
        sm_client=sm_client,
        s3_client=s3_client,
        model_name="vouch-depth-model",
        endpoint_name=args.depth_endpoint,
        tar_path=depth_tar,
        s3_bucket=args.bucket,
        s3_prefix="models",
        role_arn=args.role_arn,
        image_uri=pytorch_image,
        instance_type="ml.m5.large",
    )

    logger.info("All SageMaker models successfully deployed.")


if __name__ == "__main__":
    main()
