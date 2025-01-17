# AUTOGENERATED! DO NOT EDIT! File to edit: ../../../nbs/14_text-data-question-answering.ipynb.

# %% auto 0
__all__ = ['QAPreprocessor', 'QATextInput', 'QABatchTokenizeTransform', 'show_batch']

# %% ../../../nbs/14_text-data-question-answering.ipynb 5
import ast, warnings
from functools import reduce

from datasets import Dataset
from fastcore.all import *
from fastai.data.block import DataBlock, CategoryBlock, ColReader, ColSplitter
from fastai.imports import *
from fastai.losses import CrossEntropyLossFlat
from fastai.torch_core import *
from fastai.torch_imports import *
from transformers import AutoModelForQuestionAnswering, PretrainedConfig, PreTrainedTokenizerBase, PreTrainedModel
from transformers.utils import logging as hf_logging

from .core import TextInput, BatchTokenizeTransform, Preprocessor, first_blurr_tfm
from ..utils import get_hf_objects

# %% ../../../nbs/14_text-data-question-answering.ipynb 7
# silence all the HF warnings
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()


# %% ../../../nbs/14_text-data-question-answering.ipynb 20
class QAPreprocessor(Preprocessor):
    def __init__(
        self,
        # A Hugging Face tokenizer
        hf_tokenizer: PreTrainedTokenizerBase,
        # The number of examples to process at a time
        batch_size: int = 1000,
        # The unique identifier in the dataset. If not specified and "return_overflowing_tokens": True, an "_id" attribute
        # will be added to your dataset with its value a unique, sequential integer, assigned to each record
        id_attr: Optional[str] = None,
        # The attribute in your dataset that contains the context (where the answer is included) (default: 'context')
        ctx_attr: str = "context",
        # The attribute in your dataset that contains the question being asked (default: 'question')
        qst_attr: str = "question",
        # The attribute in your dataset that contains the actual answer (default: 'answer_text')
        ans_attr: str = "answer_text",
        # The attribute in your dataset that contains the actual answer (default: 'answer_text')
        ans_start_char_idx: str = "ans_start_char_idx",
        # The attribute in your dataset that contains the actual answer (default: 'answer_text')
        ans_end_char_idx: str = "ans_end_char_idx",
        # The attribute that should be created if your are processing individual training and validation
        # datasets into a single dataset, and will indicate to which each example is associated
        is_valid_attr: Optional[str] = "is_valid",
        # Tokenization kwargs that will be applied with calling the tokenizer (default: {"return_overflowing_tokens": True})
        tok_kwargs: dict = {"return_overflowing_tokens": True},
    ):
        # these values are mandatory
        tok_kwargs = {**tok_kwargs, "return_offsets_mapping": True}

        # shift the question and context appropriately based on the tokenizers padding strategy
        if hf_tokenizer.padding_side == "right":
            tok_kwargs["truncation"] = "only_second"
            text_attrs = [qst_attr, ctx_attr]
        else:
            tok_kwargs["truncation"] = "only_first"
            text_attrs = [ctx_attr, qst_attr]

        super().__init__(hf_tokenizer, batch_size, text_attr=text_attrs[0], text_pair_attr=text_attrs[1], tok_kwargs=tok_kwargs)
        store_attr()

    def process_df(self, training_df: pd.DataFrame, validation_df: Optional[pd.DataFrame] = None):
        df = super().process_df(training_df, validation_df)

        # a unique Id for each example is required to properly score question answering results when chunking long
        # documents (e.g., return_overflowing_tokens=True)
        chunk_docs = self.tok_kwargs.get("return_overflowing_tokens", False)
        max_length = self.tok_kwargs.get("max_length", self.hf_tokenizer.model_max_length)

        if self.id_attr is None and chunk_docs:
            df.insert(0, "_id", range(len(df)))

        # process df in mini-batches
        final_df = pd.DataFrame()
        for g, batch_df in df.groupby(np.arange(len(df)) // self.batch_size):
            final_df = final_df.append(self._process_df_batch(batch_df, chunk_docs, max_length))

        final_df.reset_index(drop=True, inplace=True)
        return final_df

    def process_hf_dataset(self, training_ds: Dataset, validation_ds: Optional[Dataset] = None):
        ds = super().process_hf_dataset(training_ds, validation_ds)
        return Dataset.from_pandas(self.process_df(pd.DataFrame(ds)))

    # ----- utility methods -----
    def _process_df_batch(self, batch_df, is_chunked, max_length):
        batch_df.reset_index(drop=True, inplace=True)

        # grab our inputs
        inputs = self._tokenize_function(batch_df.to_dict(orient="list"))

        offset_mapping = inputs.pop("offset_mapping")
        sample_map = inputs.pop("overflow_to_sample_mapping", batch_df.index.tolist())

        proc_data = []
        for idx, offsets in enumerate(offset_mapping):
            example_idx = sample_map[idx]
            row = batch_df.iloc[example_idx]
            input_ids = inputs["input_ids"][idx]
            seq_ids = inputs.sequence_ids(idx)

            # get question and context associated with the inputs at "idx"
            qst_mask = [i != 1 if self.hf_tokenizer.padding_side == "right" else i != 0 for i in seq_ids]
            qst_offsets = [offsets[i] for i, is_qst in enumerate(qst_mask) if is_qst and seq_ids[i] is not None]
            ctx_offsets = [offsets[i] for i, is_qst in enumerate(qst_mask) if not is_qst and seq_ids[i] is not None]

            proc_qst = row[self.qst_attr][min(qst_offsets)[0] : max(qst_offsets)[1]]
            proc_ctx = row[self.ctx_attr][min(ctx_offsets)[0] : max(ctx_offsets)[1]]

            # if we are chunking long documents, we need to tokenize the chunked question, context in order to correctly assign
            # the start/end token indices, else we can just the above since we are only looking at one example at a time
            if is_chunked:
                chunk_texts = (proc_qst, proc_ctx) if self.hf_tokenizer.padding_side == "right" else (proc_ctx, proc_qst)
                chunk_inputs = self.hf_tokenizer(chunk_texts[0], chunk_texts[1])
                chunk_input_ids = chunk_inputs["input_ids"]
                chunk_qst_mask = [i != 1 if self.hf_tokenizer.padding_side == "right" else i != 0 for i in chunk_inputs.sequence_ids()]
            else:
                chunk_input_ids, chunk_qst_mask = input_ids, qst_mask

            # lastly we iterate over the input tokens to see if we can fine the answer tokens within (ignoring the input tokens
            # belonging to the "question" as we only want to find answers that exist in the "context")
            tok_input = self.hf_tokenizer.convert_ids_to_tokens(chunk_input_ids)
            tok_ans = self.hf_tokenizer.tokenize(str(row[self.ans_attr]))

            start_idx, end_idx = 0, 0
            for idx, (tok, is_qst_tok) in enumerate(zip(tok_input, chunk_qst_mask)):
                try:
                    if is_qst_tok == False and tok == tok_ans[0] and tok_input[idx : idx + len(tok_ans)] == tok_ans:
                        # ensure we are within the max_length
                        last_idx = idx + len(tok_ans)
                        if last_idx < max_length:
                            start_idx, end_idx = idx, idx + len(tok_ans)
                        break
                except:
                    pass

            # update the oringal example information with the processed question, context, start/end "token" indices, and
            # a boolean indicating whether the question is answerable
            overflow_row = row.copy()
            overflow_row[f"proc_{self.qst_attr}"] = proc_qst
            overflow_row[f"proc_{self.ctx_attr}"] = proc_ctx
            overflow_row["ans_start_token_idx"] = start_idx
            overflow_row["ans_end_token_idx"] = end_idx
            overflow_row["is_answerable"] = start_idx != 0 and end_idx != 0

            proc_data.append(overflow_row)

        return pd.DataFrame(proc_data)


# %% ../../../nbs/14_text-data-question-answering.ipynb 28
class QATextInput(TextInput):
    pass


# %% ../../../nbs/14_text-data-question-answering.ipynb 30
class QABatchTokenizeTransform(BatchTokenizeTransform):
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
        # To control whether the "labels" are included in your inputs. If they are, the loss will be calculated in
        # the model's forward function and you can simply use `PreCalculatedLoss` as your `Learner`'s loss function to use it
        include_labels: bool = True,
        # The token ID that should be ignored when calculating the loss
        ignore_token_id=CrossEntropyLossFlat().ignore_index,
        # To control the length of the padding/truncation. It can be an integer or None,
        # in which case it will default to the maximum length the model can accept. If the model has no
        # specific maximum input length, truncation/padding to max_length is deactivated.
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        max_length: int = None,
        # To control the `padding` applied to your `hf_tokenizer` during tokenization. If None, will default to
        # `False` or `'do_not_pad'.
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        padding: Union[bool, str] = True,
        # To control `truncation` applied to your `hf_tokenizer` during tokenization. If None, will default to
        # `False` or `do_not_truncate`.
        # See [Everything you always wanted to know about padding and truncation](https://huggingface.co/transformers/preprocessing.html#everything-you-always-wanted-to-know-about-padding-and-truncation)
        truncation: Union[bool, str] = "only_second",
        # The `is_split_into_words` argument applied to your `hf_tokenizer` during tokenization. Set this to `True`
        # if your inputs are pre-tokenized (not numericalized)
        is_split_into_words: bool = False,
        # Any other keyword arguments you want included when using your `hf_tokenizer` to tokenize your inputs.
        tok_kwargs: dict = {},
        # Keyword arguments to apply to `BatchTokenizeTransform`
        **kwargs
    ):

        # "return_special_tokens_mask" and "return_offsets_mapping" are mandatory for extractive QA in blurr
        tok_kwargs = {**tok_kwargs, **{"return_special_tokens_mask": True, "return_offsets_mapping": True}}

        super().__init__(
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
            tok_kwargs=tok_kwargs,
            **kwargs
        )

    def encodes(self, samples, return_batch_encoding=False):
        updated_samples, batch_encoding = super().encodes(samples, return_batch_encoding=True)

        for idx, s in enumerate(updated_samples):
            # cls_index: location of CLS token (used by xlnet and xlm); is a list.index(value) for pytorch tensor's
            s[0]["cls_index"] = (s[0]["input_ids"] == self.hf_tokenizer.cls_token_id).nonzero()[0]
            # p_mask: mask with 1 for token than cannot be in the answer, else 0 (used by xlnet and xlm)
            s[0]["p_mask"] = s[0]["special_tokens_mask"]

            trgs = s[1:]
            if self.include_labels and len(trgs) > 0:
                s[0].pop("labels")  # this is added by base class, but is not needed for extractive QA
                s[0]["start_positions"] = trgs[0]
                s[0]["end_positions"] = trgs[1]

        if return_batch_encoding:
            return updated_samples, inputs

        return updated_samples


# %% ../../../nbs/14_text-data-question-answering.ipynb 45
@typedispatch
def show_batch(
    # This typedispatched `show_batch` will be called for `QuestionAnswerTextInput` typed inputs
    x: QATextInput,
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
    **kwargs
):
    # grab our tokenizer
    tfm = first_blurr_tfm(dataloaders, tfms=[QABatchTokenizeTransform])
    hf_tokenizer = tfm.hf_tokenizer

    res = L()
    for sample, input_ids, start, end in zip(samples, x, *y):
        txt = hf_tokenizer.decode(sample[0], skip_special_tokens=True)[:trunc_at]
        found = start.item() != 0 and end.item() != 0
        ans_text = hf_tokenizer.decode(input_ids[start:end], skip_special_tokens=True)
        res.append((txt, found, (start.item(), end.item()), ans_text))

    display_df(pd.DataFrame(res, columns=["text", "found", "start/end", "answer"])[:max_n])
    return ctxs

