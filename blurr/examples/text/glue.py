# AUTOGENERATED! DO NOT EDIT! File to edit: ../../../nbs/99b_text-examples-glue.ipynb.

# %% auto 0
__all__ = []

# %% ../../../nbs/99b_text-examples-glue.ipynb 5
import torch, warnings
from fastai.text.all import *

from datasets import load_dataset, concatenate_datasets
from transformers import *
from transformers.utils import logging as hf_logging

from ...text.data.core import *
from ...text.modeling.core import *
from ...text.utils import *
from ...utils import *


# %% ../../../nbs/99b_text-examples-glue.ipynb 7
# silence all the HF warnings
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()