import os

config = {
    "DATABASE_URL": os.environ["DATABASE_URL"],
    "REBBLE_AUTH": os.environ.get("REBBLE_AUTH"),
    "FIRMWARE_ROOT": os.environ.get("FIRMWARE_ROOT", "https://binaries.rebble.io/fw"),
    "MEMFAULT_TOKEN": os.environ.get("MEMFAULT_TOKEN"),
    "AWS_ACCESS_KEY": os.environ.get("AWS_ACCESS_KEY"),
    "AWS_SECRET_KEY": os.environ.get("AWS_SECRET_KEY"),
    "S3_BUCKET": os.environ.get("S3_BUCKET"),
    "S3_PATH": os.environ.get("S3_PATH", "fw/"),
    "S3_ENDPOINT": os.environ.get("S3_ENDPOINT"),
}
