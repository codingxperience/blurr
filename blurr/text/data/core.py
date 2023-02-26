# AUTOGENERATED! DO NOT EDIT! File to edit: ../../../nbs/11_text-data-core.ipynb.

# %% ../../../nbs/11_text-data-core.ipynb 4
from __future__ import annotations

import os, inspect, warnings
from dataclasses import dataclass
from functools import reduce, partial
from typing import Callable

from datasets import Dataset, load_dataset, concatenate_datasets
from fastcore.all import *
from fastai.data.block import TransformBlock
from fastai.data.core import Datasets, DataLoader, DataLoaders, TfmdDL
from fastai.imports import *
from fastai.losses import CrossEntropyLossFlat
from fastai.text.data import SortedDL
from fastai.torch_core import *
from fastai.torch_imports import *
from transformers import (
    AutoModelForSequenceClassification,
    DataCollatorWithPadding,
    PretrainedConfig,
    PreTrainedTokenizerBase,
    PreTrainedModel,
)
from transformers.utils import logging as hf_logging

from ..utils import get_hf_objects

# %% auto 0
__all__ = ['Preprocessor', 'ClassificationPreprocessor', 'TextInput', 'BatchTokenizeTransform', 'BatchDecodeTransform',
           'blurr_sort_func', 'TextBlock', 'get_blurr_tfm', 'first_blurr_tfm', 'show_batch', 'TextBatchCreator',
           'TextDataLoader', 'preproc_hf_dataset']

# %% ../../../nbs/11_text-data-core.ipynb 6
# silence all the HF warnings
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()

# %% ../../../nbs/11_text-data-core.ipynb 15
class Preprocessor:
    def __init__(
        self,
        # A Hugging Face tokenizer
        hf_tokenizer: PreTrainedTokenizerBase,
        # The number of examples to process at a time
        batch_size: int = 1000,
        # The attribute holding the text
        text_attr: str = "text",
        # The attribute holding the text_pair
        text_pair_attr: str = None,
        # The attribute that should be created if your are processing individual training and validation \
        # datasets into a single dataset, and will indicate to which each example is associated
        is_valid_attr: str = "is_valid",
        # Tokenization kwargs that will be applied with calling the tokenizer
        tok_kwargs: dict = {},
    ):
        self.hf_tokenizer = hf_tokenizer
        self.batch_size = batch_size
        self.text_attr, self.text_pair_attr = text_attr, text_pair_attr
        self.is_valid_attr = is_valid_attr
        self.tok_kwargs = tok_kwargs

        if "truncation" not in self.tok_kwargs:
            self.tok_kwargs["truncation"] = True

    def process_df(
        self, training_df: pd.DataFrame, validation_df: Optional[pd.DataFrame] = None
    ):
        df = training_df.copy()

        # concatenate the validation dataset if it is included
        if validation_df is not None:
            valid_df = validation_df.copy()
            # add an "is_valid_col" column to both training/validation DataFrames to indicate what data is part of the validation set
            if self.is_valid_attr:
                valid_df[self.is_valid_attr] = True
                df[self.is_valid_attr] = False

            df = pd.concat([df, valid_df])

        return df

    def process_hf_dataset(
        self, training_ds: Dataset, validation_ds: Optional[Dataset] = None
    ):
        ds = training_ds

        # concatenate the validation dataset if it is included
        if validation_ds is not None:
            # add an "is_valid_col" column to both training/validation DataFrames to indicate what data is part of
            # the validation set
            if self.is_valid_attr:
                validation_ds = validation_ds.add_column(
                    self.is_valid_attr, [True] * len(validation_ds)
                )
                training_ds = training_ds.add_column(
                    self.is_valid_attr, [False] * len(training_ds)
                )

            ds = concatenate_datasets([training_ds, validation_ds])

        return ds

    def _tokenize_function(self, example):
        txts = example[self.text_attr]
        txt_pairs = example[self.text_pair_attr] if self.text_pair_attr else None

        return self.hf_tokenizer(txts, txt_pairs, **self.tok_kwargs)

# %% ../../../nbs/11_text-data-core.ipynb 17
class ClassificationPreprocessor(Preprocessor):
    def __init__(
        self,
        # A Hugging Face tokenizer
        hf_tokenizer: PreTrainedTokenizerBase,
        # The number of examples to process at a time
        batch_size: int = 1000,
        # Whether the dataset should be processed for multi-label; if True, will ensure `label_attrs` are \
        # converted to a value of either 0 or 1 indiciating the existence of the class in the example
        is_multilabel: bool = False,
        # The unique identifier in the dataset
        id_attr: str = None,
        # The attribute holding the text
        text_attr: str = "text",
        # The attribute holding the text_pair
        text_pair_attr: str = None,
        # The attribute holding the label(s) of the example
        label_attrs: str | list[str] = "label",
        # The attribute that should be created if your are processing individual training and validation \
        # datasets into a single dataset, and will indicate to which each example is associated
        is_valid_attr: str = "is_valid",
        # A list indicating the valid labels for the dataset (optional, defaults to the unique set of labels \
        # found in the full dataset)
        label_mapping: list[str] = None,
        # Tokenization kwargs that will be applied with calling the tokenizer
        tok_kwargs: dict = {},
    ):
        tok_kwargs = {**tok_kwargs, "return_offsets_mapping": True}
        super().__init__(
            hf_tokenizer,
            batch_size,
            text_attr,
            text_pair_attr,
            is_valid_attr,
            tok_kwargs,
        )

        self.is_multilabel = is_multilabel
        self.id_attr = id_attr
        self.label_attrs = label_attrs
        self.label_mapping = label_mapping

    def process_df(
        self, training_df: pd.DataFrame, validation_df: Optional[pd.DataFrame] = None
    ):
        df = super().process_df(training_df, validation_df)

        # convert even single "labels" to a list to make things easier
        label_cols = listify(self.label_attrs)

        # if "is_multilabel", convert all targets to an int, 0 or 1, rounding floats if necessary
        if self.is_multilabel:
            for label_col in label_cols:
                df[label_col] = df[label_col].apply(
                    lambda v: int(bool(max(0, round(v))))
                )

        # if a "label_mapping" is included, add a "[label_col]_name" field with the label Ids converted to their label names
        if self.label_mapping:
            for label_col in label_cols:
                df[f"{label_col}_name"] = df[label_col].apply(
                    lambda v: self.label_mapping[v]
                )

        # process df in mini-batches
        final_df = pd.DataFrame()
        for g, batch_df in df.groupby(np.arange(len(df)) // self.batch_size):
            final_df = final_df.append(self._process_df_batch(batch_df))

        final_df.reset_index(drop=True, inplace=True)
        return final_df

    def process_hf_dataset(
        self, training_ds: Dataset, validation_ds: Optional[Dataset] = None
    ):
        ds = super().process_hf_dataset(training_ds, validation_ds)
        return Dataset.from_pandas(self.process_df(pd.DataFrame(ds)))

    # ----- utility methods -----
    def _process_df_batch(self, batch_df):
        batch_df.reset_index(drop=True, inplace=True)

        # grab our inputs
        inputs = self._tokenize_function(batch_df.to_dict(orient="list"))

        for txt_seq_idx, txt_attr in enumerate([self.text_attr, self.text_pair_attr]):
            if txt_attr is None:
                break

            char_idxs = []
            for idx, offset_mapping in enumerate(inputs["offset_mapping"]):
                text_offsets = [
                    offset_mapping[i]
                    for i, seq_id in enumerate(inputs.sequence_ids(idx))
                    if seq_id == txt_seq_idx
                ]
                char_idxs.append([min(text_offsets)[0], max(text_offsets)[1]])

            batch_df = pd.concat(
                [
                    batch_df,
                    pd.DataFrame(
                        char_idxs,
                        columns=[
                            f"{txt_attr}_start_char_idx",
                            f"{txt_attr}_end_char_idx",
                        ],
                    ),
                ],
                axis=1,
            )
            batch_df.insert(
                0,
                f"proc_{txt_attr}",
                batch_df.apply(
                    lambda r: r[txt_attr][
                        r[f"{txt_attr}_start_char_idx"] : r[f"{txt_attr}_end_char_idx"]
                        + 1
                    ],
                    axis=1,
                ),
            )

        return batch_df

# %% ../../../nbs/11_text-data-core.ipynb 25
class TextInput(TensorBase):
    """The base represenation of your inputs; used by the various fastai `show` methods"""

    pass

# %% ../../../nbs/11_text-data-core.ipynb 28
class BatchTokenizeTransform(Transform):
    """
    Handles everything you need to assemble a mini-batch of inputs and targets, as well as
    decode the dictionary produced as a byproduct of the tokenization process in the `encodes` method.
    """

    def __init__(
        self,
        # The abbreviation/name of your Hugging Face transformer architecture (e.b., bert, bart, etc..)
        hf_arch: str,
        # A specific configuration instance you want to use
        hf_config: PretrainedConfig,
        # A Hugging Face tokenizer
        hf_tokenizer: PreTrainedTokenizerBase,
        # A Hugging Face model
        hf_model: PreTrainedModel,
        # To control whether the "labels" are included in your inputs. If they are, the loss will be calculated in \
        # the model's forward function and you can simply use `PreCalculatedLoss` as your `Learner`'s loss function to use it
        include_labels: bool = True,
        # The token ID that should be ignored when calculating the loss
        ignore_token_id: int = CrossEntropyLossFlat().ignore_index,
        # To control the length of the padding/truncation. It can be an integer or None, \
        # in which case it will default to the maximum length the model can accept. \
        # If the model has no specific maximum input length, truncation/padding to max_length is deactivated. \
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        max_length: int = None,
        # To control the `padding` applied to your `hf_tokenizer` during tokenization. \
        # If None, will default to 'False' or 'do_not_pad'. \
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        padding: bool | str = True,
        # To control `truncation` applied to your `hf_tokenizer` during tokenization. \
        # If None, will default to 'False' or 'do_not_truncate'. \
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        truncation: bool | str = True,
        # The `is_split_into_words` argument applied to your `hf_tokenizer` during tokenization. \
        # Set this to 'True' if your inputs are pre-tokenized (not numericalized) \
        is_split_into_words: bool = False,
        # Any other keyword arguments you want included when using your `hf_tokenizer` to tokenize your inputs
        tok_kwargs: dict = {},
        # Keyword arguments to apply to `BatchTokenizeTransform`
        **kwargs
    ):
        store_attr()
        self.kwargs = kwargs

    def encodes(self, samples, return_batch_encoding=False):
        """
        This method peforms on-the-fly, batch-time tokenization of your data. In other words, your raw inputs
        are tokenized as needed for each mini-batch of data rather than requiring pre-tokenization of your full
        dataset ahead of time.
        """
        samples = L(samples)

        # grab inputs
        is_dict = isinstance(samples[0][0], dict)
        test_inp = samples[0][0]["text"] if is_dict else samples[0][0]

        if is_listy(test_inp) and not self.is_split_into_words:
            if is_dict:
                inps = [
                    (item["text"][0], item["text"][1])
                    for item in samples.itemgot(0).items
                ]
            else:
                inps = list(zip(samples.itemgot(0, 0), samples.itemgot(0, 1)))
        else:
            inps = (
                [item["text"] for item in samples.itemgot(0).items]
                if is_dict
                else samples.itemgot(0).items
            )

        inputs = self.hf_tokenizer(
            inps,
            max_length=self.max_length,
            padding=self.padding,
            truncation=self.truncation,
            is_split_into_words=self.is_split_into_words,
            return_tensors="pt",
            **self.tok_kwargs
        )

        d_keys = inputs.keys()

        # update the samples with tokenized inputs (e.g. input_ids, attention_mask, etc...), as well as extra information
        # if the inputs is a dictionary.
        # (< 2.0.0): updated_samples = [(*[{k: inputs[k][idx] for k in d_keys}], *sample[1:]) for idx, sample in enumerate(samples)]
        updated_samples = []
        for idx, sample in enumerate(samples):
            inps = {k: inputs[k][idx] for k in d_keys}
            if is_dict:
                inps = {
                    **inps,
                    **{k: v for k, v in sample[0].items() if k not in ["text"]},
                }

            trgs = sample[1:]
            if self.include_labels and len(trgs) > 0:
                inps["labels"] = trgs[0]

            updated_samples.append((*[inps], *trgs))

        if return_batch_encoding:
            return updated_samples, inputs

        return updated_samples

# %% ../../../nbs/11_text-data-core.ipynb 31
class BatchDecodeTransform(Transform):
    """A class used to cast your inputs as `input_return_type` for fastai `show` methods"""

    def __init__(
        self,
        # Used by typedispatched show methods
        input_return_type: type = TextInput,
        # The abbreviation/name of your Hugging Face transformer architecture (not required if passing in an instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_arch: str = None,
        # A Hugging Face configuration object (not required if passing in an instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_config: PretrainedConfig = None,
        # A Hugging Face tokenizer (not required if passing in an instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_tokenizer: PreTrainedTokenizerBase = None,
        # A Hugging Face model (not required if passing in an instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_model: PreTrainedModel = None,
        # Any other keyword arguments
        **kwargs
    ):
        store_attr()
        self.kwargs = kwargs

    def decodes(self, items: dict):
        """Returns the proper object and data for show related fastai methods"""
        return self.input_return_type(items["input_ids"])

# %% ../../../nbs/11_text-data-core.ipynb 34
def blurr_sort_func(
    example,
    # A Hugging Face tokenizer
    hf_tokenizer: PreTrainedTokenizerBase,
    # The `is_split_into_words` argument applied to your `hf_tokenizer` during tokenization. \
    # Set this to 'True' if your inputs are pre-tokenized (not numericalized)
    is_split_into_words: bool = False,
    # Any other keyword arguments you want to include during tokenization
    tok_kwargs: dict = {},
):
    """This method is used by the `SortedDL` to ensure your dataset is sorted *after* tokenization"""
    txt = example[0]["text"] if isinstance(example[0], dict) else example[0]
    return (
        len(txt)
        if is_split_into_words
        else len(hf_tokenizer.tokenize(txt, **tok_kwargs))
    )

# %% ../../../nbs/11_text-data-core.ipynb 36
class TextBlock(TransformBlock):
    """The core `TransformBlock` to prepare your inputs for training in Blurr with fastai's `DataBlock` API"""

    def __init__(
        self,
        # The abbreviation/name of your Hugging Face transformer architecture (not required if passing in an \
        # instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_arch: str = None,
        # A Hugging Face configuration object (not required if passing in an \
        # instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_config: PretrainedConfig = None,
        # A Hugging Face tokenizer (not required if passing in an \
        # instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_tokenizer: PreTrainedTokenizerBase = None,
        # A Hugging Face model (not required if passing in an \
        # instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_model: PreTrainedModel = None,
        # To control whether the "labels" are included in your inputs. If they are, the loss will be calculated in \
        # the model's forward function and you can simply use `PreCalculatedLoss` as your `Learner`'s loss function to use it
        include_labels: bool = True,
        # The token ID that should be ignored when calculating the loss
        ignore_token_id=CrossEntropyLossFlat().ignore_index,
        # The before_batch_tfm you want to use to tokenize your raw data on the fly \
        # (defaults to an instance of `BatchTokenizeTransform`)
        batch_tokenize_tfm: BatchTokenizeTransform = None,
        # The batch_tfm you want to decode your inputs into a type that can be used in the fastai show methods, \
        # (defaults to BatchDecodeTransform)
        batch_decode_tfm: BatchDecodeTransform = None,
        # To control the length of the padding/truncation. It can be an integer or None, \
        # in which case it will default to the maximum length the model can accept. If the model has no \
        # specific maximum input length, truncation/padding to max_length is deactivated. \
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        max_length: int = None,
        # To control the 'padding' applied to your `hf_tokenizer` during tokenization. \
        # If None, will default to 'False' or 'do_not_pad'. \
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        padding: bool | str = True,
        # To control 'truncation' applied to your `hf_tokenizer` during tokenization. \
        # If None, will default to 'False' or 'do_not_truncate'. \
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        truncation: bool | str = True,
        # The `is_split_into_words` argument applied to your `hf_tokenizer` during tokenization. \
        # Set this to `True` if your inputs are pre-tokenized (not numericalized)
        is_split_into_words: bool = False,
        # The return type your decoded inputs should be cast too (used by methods such as `show_batch`)
        input_return_type: type = TextInput,
        # The type of `DataLoader` you want created (defaults to `SortedDL`)
        dl_type: DataLoader = None,
        # Any keyword arguments you want applied to your `batch_tokenize_tfm`
        batch_tokenize_kwargs: dict = {},
        # Any keyword arguments you want applied to your `batch_decode_tfm` (will be set as a fastai `batch_tfms`)
        batch_decode_kwargs: dict = {},
        # Any keyword arguments you want your Hugging Face tokenizer to use during tokenization
        tok_kwargs: dict = {},
        # Any keyword arguments you want to have applied with generating text
        text_gen_kwargs: dict = {},
        # Any keyword arguments you want applied to `TextBlock`
        **kwargs
    ):
        if (
            not all([hf_arch, hf_config, hf_tokenizer, hf_model])
        ) and batch_tokenize_tfm is None:
            raise ValueError(
                "You must supply an hf_arch, hf_config, hf_tokenizer, hf_model -or- a BatchTokenizeTransform"
            )

        if batch_tokenize_tfm is None:
            batch_tokenize_tfm = BatchTokenizeTransform(
                hf_arch,
                hf_config,
                hf_tokenizer,
                hf_model,
                include_labels=include_labels,
                ignore_token_id=ignore_token_id,
                max_length=max_length,
                padding=padding,
                truncation=truncation,
                is_split_into_words=is_split_into_words,
                tok_kwargs=tok_kwargs.copy(),
                **batch_tokenize_kwargs.copy()
            )

        if batch_decode_tfm is None:
            batch_decode_tfm = BatchDecodeTransform(
                input_return_type=input_return_type, **batch_decode_kwargs.copy()
            )

        if dl_type is None:
            dl_sort_func = partial(
                blurr_sort_func,
                hf_tokenizer=batch_tokenize_tfm.hf_tokenizer,
                is_split_into_words=batch_tokenize_tfm.is_split_into_words,
                tok_kwargs=batch_tokenize_tfm.tok_kwargs.copy(),
            )

            dl_type = partial(SortedDL, sort_func=dl_sort_func)

        return super().__init__(
            dl_type=dl_type,
            dls_kwargs={"before_batch": batch_tokenize_tfm},
            batch_tfms=batch_decode_tfm,
        )

# %% ../../../nbs/11_text-data-core.ipynb 39
def get_blurr_tfm(
    # A list of transforms (e.g., dls.after_batch, dls.before_batch, etc...)
    tfms_list: Pipeline,
    # The transform to find
    tfm_class: Transform = BatchTokenizeTransform,
):
    """
    Given a fastai DataLoaders batch transforms, this method can be used to get at a transform
    instance used in your Blurr DataBlock
    """
    return next(filter(lambda el: issubclass(type(el), tfm_class), tfms_list), None)

# %% ../../../nbs/11_text-data-core.ipynb 41
def first_blurr_tfm(
    # Your fast.ai `DataLoaders
    dls: DataLoaders,
    # The Blurr transforms to look for in order
    tfms: list[Transform] = [BatchTokenizeTransform, BatchDecodeTransform],
):
    """
    This convenience method will find the first Blurr transform required for methods such as
    `show_batch` and `show_results`. The returned transform should have everything you need to properly
    decode and 'show' your Hugging Face inputs/targets
    """
    for tfm in tfms:
        found_tfm = get_blurr_tfm(dls.before_batch, tfm_class=tfm)
        if found_tfm:
            return found_tfm

        found_tfm = get_blurr_tfm(dls.after_batch, tfm_class=tfm)
        if found_tfm:
            return found_tfm

# %% ../../../nbs/11_text-data-core.ipynb 44
@typedispatch
def show_batch(
    # This typedispatched `show_batch` will be called for `TextInput` typed inputs
    x: TextInput,
    # Your targets
    y,
    # Your raw inputs/targets
    samples,
    # Your `DataLoaders`. This is required so as to get at the Hugging Face objects for
    # decoding them into something understandable
    dataloaders,
    # Your `show_batch` context
    ctxs=None,
    # The maximum number of items to show
    max_n=6,
    # Any truncation your want applied to your decoded inputs
    trunc_at=None,
    # Any other keyword arguments you want applied to `show_batch`
    **kwargs,
):
    # grab our tokenizer
    tfm = first_blurr_tfm(dataloaders)
    hf_tokenizer = tfm.hf_tokenizer

    # if we've included our labels list, we'll use it to look up the value of our target(s)
    trg_labels = tfm.kwargs["labels"] if ("labels" in tfm.kwargs) else None

    res = L()
    n_inp = dataloaders.n_inp

    for idx, (input_ids, label, sample) in enumerate(zip(x, y, samples)):
        if idx >= max_n:
            break

        rets = [hf_tokenizer.decode(input_ids, skip_special_tokens=True)[:trunc_at]]
        for item in sample[n_inp:]:
            if not torch.is_tensor(item):
                trg = trg_labels[int(item)] if trg_labels else item
            elif is_listy(item.tolist()):
                trg = (
                    [
                        trg_labels[idx]
                        for idx, val in enumerate(label.numpy().tolist())
                        if (val == 1)
                    ]
                    if (trg_labels)
                    else label.numpy()
                )
            else:
                trg = trg_labels[label.item()] if (trg_labels) else label.item()

            rets.append(trg)
        res.append(tuplify(rets))

    cols = ["text"] + [
        "target" if (i == 0) else f"target_{i}" for i in range(len(res[0]) - n_inp)
    ]
    display_df(pd.DataFrame(res, columns=cols)[:max_n])
    return ctxs

# %% ../../../nbs/11_text-data-core.ipynb 75
@dataclass
class TextBatchCreator:
    """
    A class that can be assigned to a `TfmdDL.create_batch` method; used to in Blurr's low-level API
    to create batches that can be used in the Blurr library
    """

    def __init__(
        self,
        # The abbreviation/name of your Hugging Face transformer architecture (e.b., bert, bart, etc..)
        hf_arch: str,
        # A specific configuration instance you want to use
        hf_config: PretrainedConfig,
        # A Hugging Face tokenizer
        hf_tokenizer: PreTrainedTokenizerBase,
        # A Hugging Face model
        hf_model: PreTrainedModel,
        # Defaults to use Hugging Face's DataCollatorWithPadding(tokenizer=hf_tokenizer)
        data_collator: type = None,
    ):
        store_attr()
        self.data_collator = (
            data_collator
            if (data_collator)
            else DataCollatorWithPadding(tokenizer=hf_tokenizer)
        )

    def __call__(self, features):
        """This method will collate your data using `self.data_collator` and add a target element to the
        returned tuples if `labels` are defined as is the case when most Hugging Face datasets
        """
        batch = self.data_collator(features)
        if isinstance(features[0], dict):
            return dict(batch), batch["labels"] if ("labels" in features[0]) else dict(
                batch
            )

        return batch

# %% ../../../nbs/11_text-data-core.ipynb 77
@delegates()
class TextDataLoader(TfmdDL):
    """
    A transformed `DataLoader` that works with Blurr.
    From the fastai docs: A `TfmDL` is described as "a DataLoader that creates Pipeline from a list of Transforms
    for the callbacks `after_item`, `before_batch` and `after_batch`. As a result, it can decode or show a processed batch.
    """

    def __init__(
        self,
        # A standard PyTorch Dataset
        dataset: torch.utils.data.dataset.Dataset | Datasets,
        # The abbreviation/name of your Hugging Face transformer architecture (not required if passing in an \
        # instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_arch: str,
        # A Hugging Face configuration object (not required if passing in an  \
        # instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_config: PretrainedConfig,
        # A Hugging Face tokenizer (not required if passing in an instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_tokenizer: PreTrainedTokenizerBase,
        # A Hugging Face model (not required if passing in an instance of `BatchTokenizeTransform` to `before_batch_tfm`)
        hf_model: PreTrainedModel,
        # An instance of `BlurrBatchCreator` or equivalent (defaults to `BlurrBatchCreator`)
        batch_creator: TextBatchCreator = None,
        # The batch_tfm used to decode Blurr batches (defaults to `BatchDecodeTransform`)
        batch_decode_tfm: BatchDecodeTransform = None,
        # Used by typedispatched show methods
        input_return_type: type = TextInput,
        # (optional) A preprocessing function that will be applied to your dataset
        preproccesing_func: Callable = None,
        # Keyword arguments to be applied to your `batch_decode_tfm`
        batch_decode_kwargs: dict = {},
        # Keyword arguments to be applied to `BlurrDataLoader`
        **kwargs,
    ):
        # if the underlying dataset needs to be preprocessed first, apply the preproccesing_func to it
        if preproccesing_func:
            dataset = preproccesing_func(dataset, hf_tokenizer, hf_model)

        # define what happens when a batch is created (e.g., this is where collation happens)
        if "create_batch" in kwargs:
            kwargs.pop("create_batch")
        if not batch_creator:
            batch_creator = TextBatchCreator(hf_arch, hf_config, hf_tokenizer, hf_model)

        # define the transform applied after the batch is created (used of show methods)
        if "after_batch" in kwargs:
            kwargs.pop("after_batch")
        if not batch_decode_tfm:
            batch_decode_tfm = BatchDecodeTransform(
                input_return_type,
                hf_arch,
                hf_config,
                hf_tokenizer,
                hf_model,
                **batch_decode_kwargs.copy(),
            )

        super().__init__(
            dataset=dataset,
            create_batch=batch_creator,
            after_batch=batch_decode_tfm,
            **kwargs,
        )
        store_attr(names="hf_arch, hf_config, hf_tokenizer, hf_model")

    def new(
        self,
        # A standard PyTorch and fastai dataset
        dataset: Union[torch.utils.data.dataset.Dataset, Datasets] = None,
        # The class you want to create an instance of (will be "self" if None)
        cls: type = None,
        #  Any additional keyword arguments you want to pass to the __init__ method of `cls`
        **kwargs,
    ):
        """
        We have to override the new method in order to add back the Hugging Face objects in this factory
        method (called for example in places like `show_results`). With the exception of the additions to the kwargs
        dictionary, the code below is pulled from the `DataLoaders.new` method as is.
        """
        # we need to add these arguments back in (these, after_batch, and create_batch will go in as kwargs)
        kwargs["hf_arch"] = self.hf_arch
        kwargs["hf_config"] = self.hf_config
        kwargs["hf_tokenizer"] = self.hf_tokenizer
        kwargs["hf_model"] = self.hf_model

        return super().new(dataset, cls, **kwargs)

# %% ../../../nbs/11_text-data-core.ipynb 83
def preproc_hf_dataset(
    # A standard PyTorch Dataset or fast.ai Datasets
    dataset: torch.utils.data.dataset.Dataset | Datasets,
    # A Hugging Face tokenizer
    hf_tokenizer: PreTrainedTokenizerBase,
    # A Hugging Face model
    hf_model: PreTrainedModel,
):
    """This method can be used to preprocess most Hugging Face Datasets for use in Blurr and other training
    libraries
    """
    if ("label") in dataset.column_names:
        dataset = dataset.rename_column("label", "labels")

    hf_model_fwd_args = list(inspect.signature(hf_model.forward).parameters.keys())
    bad_cols = set(dataset.column_names).difference(hf_model_fwd_args)
    dataset = dataset.remove_columns(bad_cols)

    dataset.set_format("torch")
    return dataset
