# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_core.ipynb.

# %% ../nbs/01_core.ipynb 4
from __future__ import annotations

import gc, importlib, sys, traceback

from accelerate.logging import get_logger
from dotenv import load_dotenv
from fastai.callback.all import *
from fastai.imports import *
from fastai.learner import *
from fastai.losses import BaseLoss, BCEWithLogitsLossFlat, CrossEntropyLossFlat
from fastai.test_utils import show_install
from fastai.torch_core import *
from fastai.torch_imports import *
from transformers import (
    AutoConfig,
    AutoTokenizer,
    PretrainedConfig,
    PreTrainedTokenizerBase,
    PreTrainedModel,
)
from transformers import logging as hf_logging

# %% auto 0
__all__ = ['logger']

# %% ../nbs/01_core.ipynb 6
# silence all the HF warnings and load environment variables
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()
logger = get_logger(__name__)

load_dotenv()