# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_utils.ipynb.

# %% ../nbs/00_utils.ipynb 3
from __future__ import annotations

import gc, importlib, sys

import torch

from fastcore.all import *
from fastai.callback.all import *
from fastai.imports import *
from fastai.learner import *
from fastai.losses import (
    BaseLoss,
    BCEWithLogitsLossFlat,
    CrossEntropyLossFlat,
    MSELossFlat,
)
from fastai.torch_core import *
from fastai.torch_imports import *

# %% auto 0
__all__ = ['Singleton', 'str_to_type', 'print_versions', 'set_seed', 'reset_memory', 'PreCalculatedLoss',
           'PreCalculatedCrossEntropyLoss', 'PreCalculatedBCELoss', 'PreCalculatedMSELoss', 'MultiTargetLoss']

# %% ../nbs/00_utils.ipynb 7
class Singleton:
    def __init__(self, cls):
        self._cls, self._instance = cls, None

    def __call__(self, *args, **kwargs):
        if self._instance == None:
            self._instance = self._cls(*args, **kwargs)
        return self._instance

# %% ../nbs/00_utils.ipynb 11
def str_to_type(
    typename: str,
) -> type:  # The name of a type as a string  # Returns the actual type
    "Converts a type represented as a string to the actual class"
    return getattr(sys.modules[__name__], typename)

# %% ../nbs/00_utils.ipynb 15
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

# %% ../nbs/00_utils.ipynb 19
# see the following threads for more info:
# - https://forums.fast.ai/t/solved-reproducibility-where-is-the-randomness-coming-in/31628?u=wgpubs
# - https://docs.fast.ai/dev/test.html#getting-reproducible-results
def set_seed(seed_value: int = 42):
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

# %% ../nbs/00_utils.ipynb 21
def reset_memory(
    # The fastai learner to delete
    learn: Learner = None,
):
    """A function which clears gpu memory."""
    if learn is not None:
        del learn
    torch.cuda.empty_cache()
    gc.collect()

# %% ../nbs/00_utils.ipynb 24
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

# %% ../nbs/00_utils.ipynb 25
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
