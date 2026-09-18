"""
WSGI entrypoint for AWS Elastic Beanstalk.

Elastic Beanstalk Python platform defaults to looking for `application.py`
with a WSGI callable named `application`.
"""

from dashboard.app import app as application

if __name__ == "__main__":
    application.run(host="0.0.0.0", port=5000)
