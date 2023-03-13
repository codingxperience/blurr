# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/12_training-token-classification.ipynb.

# %% ../../nbs/12_training-token-classification.ipynb 4
from __future__ import annotations

import ast, gc, importlib, sys, traceback

from accelerate.logging import get_logger
from dataclasses import dataclass
from dotenv import load_dotenv
from fastai.callback.all import *
from fastai.imports import *
from fastai.learner import *
from fastai.torch_core import *
from fastai.torch_imports import *
from seqeval import metrics as seq_metrics
from transformers import PreTrainedTokenizerBase, PreTrainedModel
from transformers import logging as hf_logging

from ..data.core import first_blurr_tfm
from .core import Blearner
from ..data.token_classification import TokenClassTextInput, get_token_labels_from_input_ids, get_word_labels_from_token_labels

# %% auto 0
__all__ = ['logger', 'calculate_token_class_metrics', 'TokenClassMetricsCallback', 'show_results', 'TokenAggregationStrategies',
           'BlearnerForTokenClassification']

# %% ../../nbs/12_training-token-classification.ipynb 6
# silence all the HF warnings and load environment variables
warnings.simplefilter("ignore")
hf_logging.set_verbosity_error()
logger = get_logger(__name__)

load_dotenv()

# %% ../../nbs/12_training-token-classification.ipynb 19
def calculate_token_class_metrics(pred_toks, targ_toks, metric_key):
    if metric_key == "accuracy":
        return seq_metrics.accuracy_score(targ_toks, pred_toks)

    if metric_key == "precision":
        return seq_metrics.precision_score(targ_toks, pred_toks)

    if metric_key == "recall":
        return seq_metrics.recall_score(targ_toks, pred_toks)

    if metric_key == "f1":
        return seq_metrics.f1_score(targ_toks, pred_toks)

    if metric_key == "classification_report":
        return seq_metrics.classification_report(targ_toks, pred_toks)

# %% ../../nbs/12_training-token-classification.ipynb 21
class TokenClassMetricsCallback(Callback):
    """
    A fastai friendly callback that includes accuracy, precision, recall, and f1 metrics using the
    `seqeval` library.  Additionally, this metric knows how to *not* include your 'ignore_token' in it's
    calculations.

    See [here](https://github.com/chakki-works/seqeval) for more information on `seqeval`.
    """

    def __init__(
        self,
        # The Hugging Face tokenizer used by your model
        hf_tokenizer: PreTrainedTokenizerBase,
        # The names of your labels
        label_names: list[str],
        # The token classification metrics to inclue
        tok_metrics: list[str] = ["accuracy", "precision", "recall", "f1"],
        # The token ID for labels that should be ignored when calculating metrics (e.g., CLS, SEP, PAD token Ids)
        ignore_token_id: int = CrossEntropyLossFlat().ignore_index,
        **kwargs,
    ):
        self.run_before = Recorder

        store_attr()
        self.custom_metrics_dict = {k: None for k in tok_metrics}
        self.do_setup = True

    def setup(self):
        # one time setup code here.
        if not self.do_setup:
            return

        # add custom text generation specific metrics
        custom_metric_keys = self.custom_metrics_dict.keys()
        custom_metrics = L([ValueMetric(partial(self.metric_value, metric_key=k), k) for k in custom_metric_keys])
        self.learn.metrics = self.learn.metrics + custom_metrics
        self.learn.token_classification_report = None

        self.do_setup = False

    def before_fit(self):
        self.setup()

    # --- batch begin/after phases ---
    def before_batch(self):
        pass

    def after_batch(self):
        if self.training or self.learn.y is None:
            return

        # do this only for validation set
        preds = self.pred.argmax(dim=-1)
        targs = self.yb[0]  # yb is TensorText tuple, item 0 is the data

        targets_list = [[self.label_names[l] for l in trg if l != self.ignore_token_id] for trg in targs]
        preds_list = [[self.label_names[p] for (p, l) in zip(pred, trg) if l != self.ignore_token_id] for pred, trg in zip(preds, targs)]

        self.results += [(res[0], res[1]) for res in zip(preds_list, targets_list)]

    # --- validation begin/after phases ---
    def before_validate(self):
        self.results = []

    def after_validate(self):
        if len(self.results) < 1:
            return

        preds, targs = map(list, zip(*self.results))
        for k in self.custom_metrics_dict.keys():
            self.custom_metrics_dict[k] = calculate_token_class_metrics(targs, preds, metric_key=k)

        try:
            self.learn.token_classification_report = calculate_token_class_metrics(targs, preds, "classification_report")
        except ZeroDivisionError as err:
            print(f"Couldn't calcualte classification report: {err}")

    # --- for ValueMetric metrics ---
    def metric_value(self, metric_key):
        return self.custom_metrics_dict[metric_key]

# %% ../../nbs/12_training-token-classification.ipynb 66
@typedispatch
def show_results(
    # This typedispatched `show_results` will be called for `TextInput` typed inputs
    x: TokenClassTextInput,
    # Your targets
    y,
    # Your raw inputs/targets
    samples,
    # The model's predictions
    outs,
    # Your `Learner`. This is required so as to get at the Hugging Face objects for decoding them into
    # something understandable
    learner,
    # Your `show_results` context
    ctxs=None,
    # The maximum number of items to show
    max_n=6,
    # Any truncation your want applied to your decoded inputs
    trunc_at=None,
    # Any other keyword arguments you want applied to `show_results`
    **kwargs,
):
    # grab our tokenizer
    tfm = first_blurr_tfm(learner.dls)
    hf_arch, hf_tokenizer = tfm.hf_arch, tfm.hf_tokenizer

    # if we've included our labels list, we'll use it to look up the value of our target(s)
    trg_labels = tfm.kwargs["label_names"] if ("label_names" in tfm.kwargs) else None
    if trg_labels is None and learner.dls.vocab is not None:
        trg_labels = learner.dls.vocab

    res = L()
    n_inp = learner.dls.n_inp

    n_samples = min(max_n, learner.dls.bs)
    for idx in range(n_samples):
        input_ids = x[idx]
        trgs = y[idx]
        pred = outs[idx]
        sample = samples[idx] if samples is not None else None

        # align "tokens" with labels
        tok_labels = get_token_labels_from_input_ids(hf_tokenizer, input_ids, trgs, trg_labels)
        # align "words" with labels
        word_labels = get_word_labels_from_token_labels(hf_arch, hf_tokenizer, tok_labels)
        # align "words" with "predicted" labels
        if isinstance(pred[0], str):
            pred_labels = ast.literal_eval(pred[0])
        elif torch.is_tensor(pred[0]):
            pred_labels = [trg_labels[label_id] for label_id in list(pred[0].numpy())]

        word_pred_labels = [pred_lbl for lbl_id, pred_lbl in zip(trgs, pred_labels) if lbl_id != -100]
        # stringify list of (word,label) for example
        res.append(
            [
                f"{[ (word_targ[0], word_targ[1], pred_targ) for idx, (word_targ, pred_targ) in enumerate(zip(word_labels, word_pred_labels)) if (trunc_at is None or idx < trunc_at) ]}"
            ]
        )

    display_df(pd.DataFrame(res, columns=["token / target label / predicted label"])[:max_n])
    return ctxs

# %% ../../nbs/12_training-token-classification.ipynb 144
class TokenAggregationStrategies:
    """
    Provides the equivalanet of Hugging Face's token classification pipeline's `aggregation_strategy` support across various
    token classication tasks (e.g, NER, POS, chunking, etc...)
    """

    def __init__(
        self,
        hf_tokenizer: PreTrainedTokenizerBase,
        labels: List[str],
        non_entity_label: str = "O",
    ) -> None:
        self.hf_tokenizer = hf_tokenizer
        self.labels = labels
        self.non_entity_label = non_entity_label
        self.valid_strategies = ["simple", "first", "max", "average"]

        self.uses_BI_label_strategy = False
        for lbl in self.labels:
            if lbl.startswith("I-"):
                self.uses_BI_label_strategy = True
                break

    def by_token(self, tokens, input_ids, offsets, preds, probs):
        results = []
        for tok_idx, (token, input_id, offset, pred, prob) in enumerate(zip(tokens, input_ids, offsets, preds, probs)):
            # pass over any non-entity labels and "special" tokens
            label = self.labels[pred]
            if label == self.non_entity_label or input_id.item() in self.hf_tokenizer.all_special_ids:
                continue

            start, end = offset
            results.append(
                {
                    "entity": label,
                    "score": prob[pred],
                    "word": token,
                    "start": start.item(),
                    "end": end.item(),
                }
            )

        return results

    def by_word_strategy(self, strategy_name, text, input_ids, offsets, preds, probs, word_ids=None):
        # validate `strategy_name`
        if strategy_name not in self.valid_strategies:
            raise ValueError("The 'strategy_name' is not supported by this class")

        # validate the existence of `word_ids` if the aggregation strategy = "average"
        if strategy_name == "average" and word_ids is None:
            raise ValueError("The 'average' strategy requires word_ids list")

        results = []
        idx = 0
        while idx < len(preds):
            pred = preds[idx]
            label = self.labels[pred]

            # pass over any non-entity labels and "special" tokens
            if label == self.non_entity_label or input_ids[idx].item() in self.hf_tokenizer.all_special_ids:
                idx += 1
                continue

            # Remove the B- or I-
            label = label[2:] if self.uses_BI_label_strategy else label
            start, end = offsets[idx]

            all_scores = []
            all_scores.append(probs[idx][pred])

            word_scores = {}
            if strategy_name == "average":
                word_scores[word_ids[idx]] = [probs[idx][pred]]

            lbl_to_search = f"I-{label}" if self.uses_BI_label_strategy else label
            while idx + 1 < len(preds) and self.labels[preds[idx + 1]] == lbl_to_search:
                idx += 1
                _, end = offsets[idx]

                pred = preds[idx]

                if strategy_name == "average":
                    if word_ids[idx] in word_scores:
                        word_scores[word_ids[idx]].append(probs[idx][pred])
                    else:
                        word_scores[word_ids[idx]] = [probs[idx][pred]]

                if strategy_name != "first":
                    all_scores.append(probs[idx][pred])

            # The score is the mean of all the scores of the tokens in that grouped entity
            if strategy_name == "average":
                score = np.mean([np.mean(v).item() for k, v in word_scores.items()])
            else:
                score = np.max(all_scores).item() if strategy_name == "max" else np.mean(all_scores).item()

            word = text[start:end]
            results.append(
                {
                    "entity_group": label,
                    "score": score,
                    "word": word,
                    "start": start.item(),
                    "end": end.item(),
                }
            )

            idx += 1

        return results

# %% ../../nbs/12_training-token-classification.ipynb 145
@patch
def blurr_predict_tokens(
    self: Learner,
    # The str (or list of strings) you want to get token classification predictions for
    items: Union[str, List[str]],
    # How entities are grouped and scored
    aggregation_strategy: str = "simple",
    # The label used to idendity non-entity related words/tokens
    non_entity_label: str = "O",
    # If using a slow tokenizer, users will need to prove a `slow_word_ids_func` that accepts a
    # tokenizzer, example index, and a batch encoding as arguments and in turn returnes the
    # equavlient of fast tokenizer's `word_ids``
    slow_word_ids_func: Optional[Callable] = None,
):
    if not is_listy(items):
        items = [items]

    tfm = first_blurr_tfm(self.dls)
    batch_tok_tfm = get_blurr_tfm(self.dls.before_batch, tfm_class=BatchTokenizeTransform)

    hf_tokenizer = tfm.hf_tokenizer

    strategies = TokenAggregationStrategies(hf_tokenizer, self.dls.vocab, non_entity_label)

    inputs = hf_tokenizer(
        items,
        return_offsets_mapping=True,
        return_tensors="pt",
        padding=True,
        truncation=True,
    )
    inputs_offsets = inputs["offset_mapping"]
    inputs_input_ids = inputs["input_ids"]

    # run inputs through model
    model_inputs = {k: v.to(self.model.hf_model.device) for k, v in inputs.items()}
    outputs = self.model(model_inputs)

    # fetch probabilities and predictions
    probabilities = F.softmax(outputs.logits, dim=-1).tolist()
    predictions = outputs.logits.argmax(dim=-1).tolist()

    # build our results
    results = []
    for input_idx, (text, input_ids, offsets, preds, probs) in enumerate(
        zip(items, inputs_input_ids, inputs_offsets, predictions, probabilities)
    ):
        # build our results for the current input
        tokens = inputs.tokens(input_idx)
        word_ids = inputs.word_ids(input_idx) if hf_tokenizer.is_fast else slow_word_ids_func(hf_tokenizer, input_idx, inputs)

        if aggregation_strategy == "token":
            results.append(strategies.by_token(tokens, input_ids, offsets, preds, probs))
        else:
            results.append(
                strategies.by_word_strategy(
                    aggregation_strategy,
                    text,
                    input_ids,
                    offsets,
                    preds,
                    probs,
                    word_ids,
                )
            )
    return results

# %% ../../nbs/12_training-token-classification.ipynb 155
@delegates(Blearner.__init__)
class BlearnerForTokenClassification(Blearner):
    def __init__(self, dls: DataLoaders, hf_model: PreTrainedModel, **kwargs):
        super().__init__(dls, hf_model, **kwargs)

    def predict(self, text):
        return self.blurr_predict_tokens(text)

    def get_metrics_cb(self):
        tfm = first_blurr_tfm(self.dls)

        # if we've included our labels list, we'll use it to look up the value of our target(s)
        trg_labels = tfm.kwargs["label_names"] if ("label_names" in tfm.kwargs) else None
        if trg_labels is None and self.dls.vocab is not None:
            trg_labels = self.dls.vocab

        return TokenClassMetricsCallback(hf_tokenizer=tfm.hf_tokenizer, label_names=trg_labels)
