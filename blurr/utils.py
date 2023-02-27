# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_utils.ipynb.

# %% ../nbs/00_utils.ipynb 3
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
__all__ = ['logger', 'DEFAULT_SEED', 'Singleton', 'str_to_type', 'set_seed', 'print_versions', 'print_dev_environment',
           'clean_ipython_hist', 'clean_tb', 'clean_memory', 'PreCalculatedLoss', 'PreCalculatedCrossEntropyLoss',
           'PreCalculatedBCELoss', 'PreCalculatedMSELoss', 'MultiTargetLoss', 'get_hf_objects']

# %% ../nbs/00_utils.ipynb 5
# silence all the HF warnings and load environment variables
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()
logger = get_logger(__name__)

load_dotenv()

# %% ../nbs/00_utils.ipynb 8
DEFAULT_SEED = int(os.getenv("RANDOM_SEED", 2023))

# %% ../nbs/00_utils.ipynb 10
class Singleton:
    def __init__(self, cls):
        self._cls, self._instance = cls, None

    def __call__(self, *args, **kwargs):
        if self._instance == None:
            self._instance = self._cls(*args, **kwargs)
        return self._instance

# %% ../nbs/00_utils.ipynb 12
def str_to_type(
    typename: str,
) -> type:  # The name of a type as a string  # Returns the actual type
    "Converts a type represented as a string to the actual class"
    return getattr(sys.modules[__name__], typename)

# %% ../nbs/00_utils.ipynb 15
# see the following threads for more info:
# - https://forums.fast.ai/t/solved-reproducibility-where-is-the-randomness-coming-in/31628?u=wgpubs
# - https://docs.fast.ai/dev/test.html#getting-reproducible-results
def set_seed(seed_value: int = 2023):
    """This needs to be ran before creating your DataLoaders, before creating your Learner, and before each call
    to your fit function to help ensure reproducibility.
    """
    np.random.seed(seed_value)  # cpu vars
    torch.manual_seed(seed_value)  # cpu vars
    random.seed(seed_value)  # python

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed_value)
        torch.cuda.manual_seed_all(seed_value)  # gpu vars
        torch.backends.cudnn.deterministic = True  # needed
        torch.backends.cudnn.benchmark = False

# %% ../nbs/00_utils.ipynb 19
def print_versions(
    # A string of space delimited package names or a list of package names
    packages: str
    | list[str],
):
    """Prints the name and version of one or more packages in your environment"""
    packages = packages.split(" ") if isinstance(packages, str) else packages

    for item in packages:
        item = item.strip()
        print(f"{item}: {importlib.import_module(item).__version__}")

# %% ../nbs/00_utils.ipynb 22
def print_dev_environment():
    """Provides details on your development environment including packages installed, cuda/cudnn availability, GPUs, etc."""
    print(show_install())

# %% ../nbs/00_utils.ipynb 25
def clean_ipython_hist():
    # Code in this function mainly copied from IPython source
    if not "get_ipython" in globals():
        return

    ip = get_ipython()
    user_ns = ip.user_ns
    ip.displayhook.flush()
    pc = ip.displayhook.prompt_count + 1

    for n in range(1, pc):
        user_ns.pop("_i" + repr(n), None)

    user_ns.update(dict(_i="", _ii="", _iii=""))
    hm = ip.history_manager
    hm.input_hist_parsed[:] = [""] * pc
    hm.input_hist_raw[:] = [""] * pc
    hm._i = hm._ii = hm._iii = hm._i00 = ""

# %% ../nbs/00_utils.ipynb 26
def clean_tb():
    # h/t Piotr Czapla
    if hasattr(sys, "last_traceback"):
        traceback.clear_frames(sys.last_traceback)
        delattr(sys, "last_traceback")
    if hasattr(sys, "last_type"):
        delattr(sys, "last_type")
    if hasattr(sys, "last_value"):
        delattr(sys, "last_value")

# %% ../nbs/00_utils.ipynb 27
def clean_memory(
    # The fastai learner to delete
    learn: Learner = None,
):
    """A function which clears gpu memory."""
    if learn is not None:
        del learn
    clean_tb()
    clean_ipython_hist()
    torch.cuda.empty_cache()
    gc.collect()

# %% ../nbs/00_utils.ipynb 31
class PreCalculatedLoss(BaseLoss):
    """
    If you want to let your Hugging Face model calculate the loss for you, make sure you include the `labels` argument in your inputs and use
    `PreCalculatedLoss` as your loss function. Even though we don't really need a loss function per se, we have to provide a custom loss class/function
    for fastai to function properly (e.g. one with a `decodes` and `activation` methods).  Why?  Because these methods will get called in methods
    like `show_results` to get the actual predictions.

    Note: The Hugging Face models ***will always*** calculate the loss for you ***if*** you pass a `labels` dictionary along with your other inputs
    (so only include it if that is what you intend to happen)
    """

    def __call__(self, inp, targ, **kwargs):
        return tensor(0.0)


class PreCalculatedCrossEntropyLoss(PreCalculatedLoss, CrossEntropyLossFlat):
    pass


class PreCalculatedBCELoss(PreCalculatedLoss, BCEWithLogitsLossFlat):
    pass


class PreCalculatedMSELoss(PreCalculatedLoss):
    def __init__(self, *args, axis=-1, floatify=True, **kwargs):
        super().__init__(
            nn.MSELoss, *args, axis=axis, floatify=floatify, is_2d=False, **kwargs
        )

# %% ../nbs/00_utils.ipynb 32
class MultiTargetLoss(Module):
    """
    Provides the ability to apply different loss functions to multi-modal targets/predictions.

    This new loss function can be used in many other multi-modal architectures, with any mix of loss functions.
    For example, this can be ammended to include the `is_impossible` task, as well as the start/end token tasks
    in the SQUAD v2 dataset (or in any extractive question/answering task)
    """

    def __init__(
        self,
        # The loss function for each target
        loss_classes: list[Callable] = [CrossEntropyLossFlat, CrossEntropyLossFlat],
        # Any kwargs you want to pass to the loss functions above
        loss_classes_kwargs: list[dict] = [{}, {}],
        # The weights you want to apply to each loss (default: [1,1])
        weights: list[float] | list[int] = [1, 1],
        # The `reduction` parameter of the lass function (default: 'mean')
        reduction: str = "mean",
    ):
        loss_funcs = [
            cls(reduction=reduction, **kwargs)
            for cls, kwargs in zip(loss_classes, loss_classes_kwargs)
        ]
        store_attr(self=self, names="loss_funcs, weights")
        self._reduction = reduction

    # custom loss function must have either a reduction attribute or a reduction argument (like all fastai and
    # PyTorch loss functions) so that the framework can change this as needed (e.g., when doing lear.get_preds
    # it will set = 'none'). see this forum topic for more info: https://bit.ly/3br2Syz
    @property
    def reduction(self):
        return self._reduction

    @reduction.setter
    def reduction(self, v):
        self._reduction = v
        for lf in self.loss_funcs:
            lf.reduction = v

    def forward(self, outputs, *targets):
        loss = 0.0
        for i, loss_func, weights, output, target in zip(
            range(len(outputs)), self.loss_funcs, self.weights, outputs, targets
        ):
            loss += weights * loss_func(output, target)

        return loss

    def activation(self, outs):
        acts = [self.loss_funcs[i].activation(o) for i, o in enumerate(outs)]
        return acts

    def decodes(self, outs):
        decodes = [self.loss_funcs[i].decodes(o) for i, o in enumerate(outs)]
        return decodes

# %% ../nbs/00_utils.ipynb 34
def get_hf_objects(
    pretrained_model_name_or_path: str | os.PathLike,
    model_cls: PreTrainedModel,
    config: PretrainedConfig | str | os.PathLike = None,
    tokenizer_cls: PreTrainedTokenizerBase = None,
    config_kwargs: dict = {},
    tokenizer_kwargs: dict = {},
    model_kwargs: dict = {},
    cache_dir: str | os.PathLike = None,
) -> tuple[str, PretrainedConfig, PreTrainedTokenizerBase, PreTrainedModel]:
    """
    Given at minimum a `pretrained_model_name_or_path` and `model_cls (such as
    `AutoModelForSequenceClassification"), this method returns all the Hugging Face objects you need to train
    a model using Blurr
    """
    # config
    if config is None:
        config = AutoConfig.from_pretrained(
            pretrained_model_name_or_path, cache_dir=cache_dir, **config_kwargs
        )

    # tokenizer (gpt2, roberta, bart (and maybe others) tokenizers require a prefix space)
    if any(
        s in pretrained_model_name_or_path
        for s in ["gpt2", "roberta", "bart", "longformer"]
    ):
        tokenizer_kwargs = {**{"add_prefix_space": True}, **tokenizer_kwargs}

    if tokenizer_cls is None:
        tokenizer = AutoTokenizer.from_pretrained(
            pretrained_model_name_or_path, cache_dir=cache_dir, **tokenizer_kwargs
        )
    else:
        tokenizer = tokenizer_cls.from_pretrained(
            pretrained_model_name_or_path, cache_dir=cache_dir, **tokenizer_kwargs
        )

    # model
    model = model_cls.from_pretrained(
        pretrained_model_name_or_path,
        config=config,
        cache_dir=cache_dir,
        **model_kwargs
    )

    # arch
    try:
        arch = model.__module__.split(".")[2]
    except:
        arch = "unknown"

    return (arch, config, tokenizer, model)
