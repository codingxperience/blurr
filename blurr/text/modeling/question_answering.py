# AUTOGENERATED! DO NOT EDIT! File to edit: ../../../nbs/14_text-modeling-question-answering.ipynb.

# %% auto 0
__all__ = ['squad_metric', 'QAModelCallback', 'QAMetricsCallback', 'compute_qa_metrics', 'PreCalculatedQALoss', 'show_results',
           'BlearnerForQuestionAnswering']

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 5
import os, ast, inspect, warnings
from typing import Any, Callable, Dict, List, Optional, Union, Type

from datasets import load_metric
from fastcore.all import *
from fastai.callback.all import *
from fastai.data.block import (
    DataBlock,
    CategoryBlock,
    ColReader,
    ItemGetter,
    ColSplitter,
    RandomSplitter,
)
from fastai.data.core import DataLoader, DataLoaders, TfmdDL
from fastai.imports import *
from fastai.learner import *
from fastai.losses import CrossEntropyLossFlat
from fastai.optimizer import Adam, OptimWrapper, params
from fastai.torch_core import *
from fastai.torch_imports import *
from seqeval import metrics as seq_metrics
from transformers import AutoModelForQuestionAnswering, PreTrainedModel
from transformers.utils import logging as hf_logging

from ..data.core import TextBlock, TextDataLoader, first_blurr_tfm
from blurr.text.data.question_answering import (
    QAPreprocessor,
    QATextInput,
    QABatchTokenizeTransform,
)
from .core import BaseModelCallback, Blearner
from ..utils import get_hf_objects
from ...utils import PreCalculatedLoss, MultiTargetLoss

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 7
# metrics we'll use in extractive qa
squad_metric = load_metric("squad")

# silence all the HF warnings
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 20
class QAModelCallback(BaseModelCallback):
    """The prediction is a combination start/end logits"""

    def after_pred(self):
        super().after_pred()
        self.learn.pred = (self.pred.start_logits, self.pred.end_logits)

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 23
class QAMetricsCallback(Callback):
    def __init__(
        self,
        compute_metrics_func,
        validation_ds,
        qa_metrics=["exact_match", "f1"],
        **kwargs
    ):
        self.run_before = Recorder

        store_attr()
        self.custom_metrics_dict = {k: None for k in qa_metrics}
        self.do_setup = True

    def setup(self):
        # one time setup code here.
        if not self.do_setup:
            return

        # grab the hf_tokenizer from the TokenClassBatchTokenizeTransform
        tfm = first_blurr_tfm(self.learn.dls, tfms=[QABatchTokenizeTransform])
        self.hf_tokenizer = tfm.hf_tokenizer
        self.tok_kwargs = tfm.tok_kwargs

        # add custom question answering specific metrics
        custom_metrics = L(
            [
                ValueMetric(partial(self.metric_value, metric_key=k), k)
                for k in self.qa_metrics
            ]
        )
        self.learn.metrics = self.learn.metrics + custom_metrics

        self.do_setup = False

    def before_fit(self):
        self.setup()

    # --- batch before/after phases ---
    def before_batch(self):
        if self.training or self.learn.y is None:
            return

        self.batch_inputs = {
            k: v.cpu().detach().numpy() if isinstance(v, Tensor) else v
            for k, v in self.x.items()
        }

    def after_batch(self):
        if self.training or self.learn.y is None:
            return

        for i in range(len(self.batch_inputs["input_ids"])):
            batch_inps = {k: self.batch_inputs[k][i] for k in self.batch_inputs.keys()}
            self.results.append(
                {
                    **batch_inps,
                    "start_logits": self.pred[0][i].cpu().detach().numpy(),
                    "end_logits": self.pred[1][i].cpu().detach().numpy(),
                }
            )

    # --- validation begin/after phases ---
    def before_validate(self):
        self.results = []

    def after_validate(self):
        if len(self.results) < 1:
            return

        metric_vals_d = self.compute_metrics_func(
            self.results, self.validation_ds, self.hf_tokenizer, self.tok_kwargs
        )
        for k, v in metric_vals_d.items():
            self.custom_metrics_dict[k] = v

    # --- for ValueMetric metrics ---
    def metric_value(self, metric_key):
        return self.custom_metrics_dict[metric_key]

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 24
def compute_qa_metrics(
    results, dataset, hf_tokenizer, tok_kwargs, id_attr="id", n_best=20
):
    # what is the max length for our inputs?
    max_length = tok_kwargs.get("max_length", hf_tokenizer.model_max_length)

    # map examples to chunks indicies that are part of the
    example_to_chunks = collections.defaultdict(list)
    for idx, chunk in enumerate(results):
        example_to_chunks[chunk[id_attr]].append(idx)

    predicted_answers = []
    for item_idx, item in enumerate(dataset):
        example_id = item[id_attr]

        answers = []
        for chunk_idx in example_to_chunks[example_id]:
            chunk = results[chunk_idx]
            input_ids = chunk["input_ids"]
            start_logits = chunk["start_logits"]
            end_logits = chunk["end_logits"]

            start_indexes = np.argsort(start_logits)[-1 : -n_best - 1 : -1].tolist()
            end_indexes = np.argsort(end_logits)[-1 : -n_best - 1 : -1].tolist()

            for s_idx, start_index in enumerate(start_indexes):
                for e_idx, end_index in enumerate(end_indexes):
                    # Skip answers that are not fully in the context
                    if start_index == 0 and end_index == 0:
                        continue

                    # Skip answers with a length that is either < 0 or > max_answer_length
                    if (
                        end_index < start_index
                        or end_index - start_index + 1 > max_length
                    ):
                        continue

                    answer = {
                        "text": hf_tokenizer.decode(
                            input_ids[start_index:end_index], skip_special_tokens=True
                        ),
                        "logit_score": start_logits[start_index]
                        + end_logits[end_index],
                    }
                    answers.append(answer)

        # select the answer with the best score
        if len(answers) > 0:
            best_answer = max(answers, key=lambda x: x["logit_score"])
            predicted_answers.append(
                {"id": example_id, "prediction_text": best_answer["text"]}
            )
        else:
            predicted_answers.append({"id": example_id, "prediction_text": ""})

    ref_answers = [
        {"id": item["id"], "answers": item["answers"]}
        for item_idx, item in enumerate(dataset)
    ]

    metric_vals_d = squad_metric.compute(
        predictions=predicted_answers, references=ref_answers
    )
    return metric_vals_d

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 27
class PreCalculatedQALoss(PreCalculatedLoss):
    def __init__(self, *args, axis=-1, **kwargs):
        super().__init__(nn.CrossEntropyLoss, *args, axis=axis, **kwargs)

    def __call__(self, inp, targ, targ2, **kwargs):
        return tensor(0.0)

    def decodes(self, x):
        return x[0].argmax(dim=self.axis), x[1].argmax(dim=self.axis)

    def activation(self, x):
        return F.softmax(x[0], dim=self.axis), F.softmax(x[1], dim=self.axis)

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 37
@typedispatch
def show_results(
    # This typedispatched `show_results` will be called for `QuestionAnswerTextInput` typed inputs
    x: QATextInput,
    # The targets
    y,
    # Your raw inputs/targets
    samples,
    # The model's predictions
    outs,
    # Your `Learner`. This is required so as to get at the Hugging Face objects for decoding them into
    # something understandable
    learner,
    # Whether you want to remove special tokens during decoding/showing the outputs
    skip_special_tokens=True,
    # Your `show_results` context
    ctxs=None,
    # The maximum number of items to show
    max_n=6,
    # Any truncation your want applied to your decoded inputs
    trunc_at=None,
    # Any other keyword arguments you want applied to `show_results`
    **kwargs
):
    tfm = first_blurr_tfm(learner.dls, tfms=[QABatchTokenizeTransform])
    hf_tokenizer = tfm.hf_tokenizer

    res = L()
    for sample, input_ids, start, end, pred in zip(samples, x, *y, outs):
        txt = hf_tokenizer.decode(sample[0], skip_special_tokens=True)[:trunc_at]
        found = start.item() != 0 and end.item() != 0
        ans_text = hf_tokenizer.decode(input_ids[start:end], skip_special_tokens=False)

        pred_ans_toks = hf_tokenizer.convert_ids_to_tokens(
            input_ids, skip_special_tokens=False
        )[int(pred[0]) : int(pred[1])]
        pred_ans_txt = hf_tokenizer.convert_tokens_to_string(pred_ans_toks)

        res.append(
            (
                txt,
                found,
                (start.item(), end.item()),
                ans_text,
                (int(pred[0]), int(pred[1])),
                pred_ans_txt,
            )
        )

    display_df(
        pd.DataFrame(
            res,
            columns=[
                "text",
                "found",
                "start/end",
                "answer",
                "pred start/end",
                "pred answer",
            ],
        )
    )
    return ctxs

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 44
@patch
def blurr_predict_answers(
    self: Learner,
    # The str (or list of strings) you want to get token classification predictions for
    question_contexts: Union[dict, List[dict]],
    # If using a slow tokenizer, users will need to prove a `slow_word_ids_func` that accepts a
    # tokenizzer, example index, and a batch encoding as arguments and in turn returnes the
    # equavlient of fast tokenizer's `word_ids``
    slow_word_ids_func: Optional[Callable] = None,
):
    if not is_listy(question_contexts):
        question_contexts = [question_contexts]

    tfm = first_blurr_tfm(self.dls, tfms=[QABatchTokenizeTransform])
    hf_tokenizer = tfm.hf_tokenizer
    tok_kwargs = tfm.tok_kwargs
    tok_kwargs["return_overflowing_tokens"] = True
    tok_kwargs["truncation"] = (
        "only_second" if hf_tokenizer.padding_side == "right" else "only_first"
    )

    results = []
    for qc in question_contexts:
        inps = (
            [qc["question"], qc["context"]]
            if hf_tokenizer.padding_side == "right"
            else [qc["context"], qc["question"]]
        )

        inputs = hf_tokenizer(
            *inps,
            max_length=tfm.max_length,
            padding=tfm.padding,
            return_tensors="pt",
            **tok_kwargs
        )
        inputs_offsets = inputs["offset_mapping"]

        # run inputs through model
        model_inputs = {k: v.to(self.model.hf_model.device) for k, v in inputs.items()}
        outputs = self.model(model_inputs)

        # grab our start/end logits
        start_logits = outputs.start_logits
        end_logits = outputs.end_logits

        # mask any tokens that shouldn't be considered
        seq_ids = inputs.sequence_ids()
        # mask question tokens
        ignore_mask = [
            i != 1 if hf_tokenizer.padding_side == "right" else i != 0 for i in seq_ids
        ]
        # unmask the [CLS] token
        ignore_mask[0] = False
        # mask all the [PAD] tokens
        ignore_mask = torch.logical_or(
            torch.tensor(ignore_mask)[None], (inputs["attention_mask"] == 0)
        )

        start_logits[ignore_mask] = tfm.ignore_token_id
        end_logits[ignore_mask] = tfm.ignore_token_id

        # grab our start/end probabilities
        start_probs = F.softmax(start_logits, dim=-1)
        end_probs = F.softmax(end_logits, dim=-1)

        # get scores for each chunk
        candidates = []
        for offset_idx, (chunk_start_probs, chunk_end_probs) in enumerate(
            zip(start_probs, end_probs)
        ):
            scores = chunk_start_probs[:, None] * chunk_end_probs[None, :]
            idx = torch.triu(scores).argmax().item()

            start_idx = idx // scores.shape[0]
            end_idx = idx % scores.shape[0]
            score = scores[start_idx, end_idx].item()
            candidates.append((offset_idx, start_idx, end_idx, score))

        # sort our candidates by score
        candidates.sort(key=lambda el: el[3], reverse=True)

        # return our best answer
        best = candidates[0]
        if best[1] == 0 and best[2] == 0:
            results.append({"answer": None, "start": 0, "end": 0, "score": best[3]})
        else:
            start_char_idx = inputs_offsets[best[0]][best[1]][0]
            end_char_idx = inputs_offsets[best[0]][best[2] - 1][1]
            ans = inps[1][start_char_idx:end_char_idx].strip()

            results.append(
                {
                    "answer": ans,
                    "start": start_char_idx.item(),
                    "end": end_char_idx.item(),
                    "score": best[3],
                }
            )

    # build our results
    return results

# %% ../../../nbs/14_text-modeling-question-answering.ipynb 54
@delegates(Blearner.__init__)
class BlearnerForQuestionAnswering(Blearner):
    def __init__(self, dls: DataLoaders, hf_model: PreTrainedModel, **kwargs):
        kwargs["loss_func"] = kwargs.get("loss_func", PreCalculatedQALoss())
        super().__init__(dls, hf_model, base_model_cb=QAModelCallback, **kwargs)

    @classmethod
    def get_model_cls(self):
        return AutoModelForQuestionAnswering

    @classmethod
    def _get_x(cls, x, qst, ctx, id=None, padding_side="right"):
        inps = {}
        inps["text"] = (
            (x[qst], x[ctx]) if (padding_side == "right") else (x[ctx], x[qst])
        )

        if id is not None:
            inps["id"] = x[id]

        return inps

    @classmethod
    def from_data(
        cls,
        # Your raw dataset. Supports DataFrames, Hugging Face Datasets, as well as file paths
        # to .csv, .xlsx, .xls, and .jsonl files
        data: Union[pd.DataFrame, Path, str, List[Dict]],
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # The maximum sequence length to constrain our data
        max_seq_len: int = None,
        # The unique identifier in the dataset. If not specified and "return_overflowing_tokens": True, an "_id" attribute
        # will be added to your dataset with its value a unique, sequential integer, assigned to each record
        id_attr: Optional[str] = None,
        # The attribute in your dataset that contains the context (where the answer is included) (default: 'context')
        context_attr: str = "context",
        # The attribute in your dataset that contains the question being asked (default: 'question')
        question_attr: str = "question",
        # The attribute in your dataset that contains the tokenized answer start (default: 'tok_answer_start')
        tok_ans_start_attr: str = "ans_start_token_idx",
        # The attribute in your dataset that contains the tokenized answer end(default: 'tok_answer_end')
        tok_ans_end_attr: str = "ans_end_token_idx",
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Optional[Callable] = None,
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs={},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs={},
    ):
        # if we get a path/str then we're loading something like a .csv file
        if isinstance(data, Path) or isinstance(data, str):
            content_type = mimetypes.guess_type(data)[0]
            if (
                content_type
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ):
                data = pd.read_excel(data)
            elif content_type == "text/csv":
                data = pd.read_csv(data)
            elif content_type == "application/json":
                data = pd.read_json(data, orient="records")
            else:
                raise ValueError("'data' must be a .xlsx, .xls, .csv, or .jsonl file")

            data = pd.read_csv(data)

        # infer our datablock splitter if None
        if dblock_splitter is None:
            dblock_splitter = (
                ColSplitter() if hasattr(data, "is_valid") else RandomSplitter()
            )

        hf_arch, hf_config, hf_tokenizer, hf_model = get_hf_objects(
            pretrained_model_name_or_path, model_cls=cls.get_model_cls()
        )

        # potentially used by our preprocess_func, it is the basis for our CategoryBlock vocab
        if max_seq_len is None:
            max_seq_len = hf_config.get("max_position_embeddings", 128)

        # bits required by our "before_batch_tfm" and DataBlock
        vocab = list(range(max_seq_len))
        padding_side = hf_tokenizer.padding_side

        # define DataBlock and DataLoaders
        before_batch_tfm = QABatchTokenizeTransform(
            hf_arch, hf_config, hf_tokenizer, hf_model, max_length=max_seq_len
        )
        blocks = (
            TextBlock(
                batch_tokenize_tfm=before_batch_tfm, input_return_type=QATextInput
            ),
            CategoryBlock(vocab=vocab),
            CategoryBlock(vocab=vocab),
        )
        dblock = DataBlock(
            blocks=blocks,
            get_x=partial(
                cls._get_x,
                qst=question_attr,
                ctx=context_attr,
                id=id_attr,
                padding_side=padding_side,
            ),
            get_y=[ItemGetter(tok_ans_start_attr), ItemGetter(tok_ans_end_attr)],
            splitter=dblock_splitter,
            n_inp=1,
        )

        dls = dblock.dataloaders(data, **dl_kwargs.copy())

        # return BLearner instance
        return cls(dls, hf_model, **learner_kwargs.copy())
