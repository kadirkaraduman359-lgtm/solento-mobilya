import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Railway volume mount path: /data, yoksa uygulama klasörü
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "solento-gizli-anahtar-2024")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(DATA_DIR, "depo.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
