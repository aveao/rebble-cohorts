from flask import Flask

from .api import init_app as init_api
from .cli import init_app as init_cli
from .models import init_app as init_models
from .settings import config

app = Flask(__name__)
app.config.update(config)

init_models(app)
init_api(app)
init_cli(app)
