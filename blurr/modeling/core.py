# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/01_modeling-core.ipynb (unless otherwise specified).

__all__ = ['hf_splitter', 'HF_BaseModelWrapper', 'HF_PreCalculatedLoss', 'HF_BaseModelCallback', 'Blearner',
           'BlearnerForSequenceClassification']

# Cell
import os, inspect
from typing import Any, Callable, Dict, List, Optional, Union, Type

from fastcore.all import *
from fastai.callback.all import *
from fastai.data.block import DataBlock, ColReader, CategoryBlock, MultiCategoryBlock, ColSplitter, RandomSplitter
from fastai.data.core import DataLoader, DataLoaders, TfmdDL
from fastai.imports import *
from fastai.learner import *
from fastai.losses import CrossEntropyLossFlat
from fastai.optimizer import Adam, OptimWrapper, params
from fastai.metrics import accuracy, F1Score, accuracy_multi, F1ScoreMulti
from fastai.torch_core import *
from fastai.torch_imports import *
from fastprogress.fastprogress import progress_bar, master_bar
from transformers import AutoModelForSequenceClassification, logging, PretrainedConfig, PreTrainedTokenizerBase, PreTrainedModel

from ..utils import BLURR, set_seed
from ..data.core import HF_TextBlock, HF_BaseInput, first_blurr_tfm

logging.set_verbosity_error()


# Cell
def hf_splitter(m: Module):
    """Splits the Hugging Face model based on various model architecture conventions"""
    model = m.hf_model if (hasattr(m, "hf_model")) else m
    root_modules = list(model.named_children())
    top_module_name, top_module = root_modules[0]

    groups = L([m for m_name, m in list(top_module.named_children())])
    groups += L([m for m_name, m in root_modules[1:]])

    return groups.map(params).filter(lambda el: len(el) > 0)


# Cell
class HF_BaseModelWrapper(Module):
    def __init__(
        self,
        # Your Hugging Face model
        hf_model: PreTrainedModel,
        # If True, hidden_states will be returned and accessed from Learner
        output_hidden_states: bool = False,
        # If True, attentions will be returned and accessed from Learner
        output_attentions: bool = False,
        # Any additional keyword arguments you want passed into your models forward method
        hf_model_kwargs={},
    ):
        super().__init__()

        store_attr(self=self, names="output_hidden_states, output_attentions, hf_model_kwargs")
        self.hf_model = hf_model.cuda() if torch.cuda.is_available() else hf_model

        self.hf_model_fwd_args = list(inspect.signature(self.hf_model.forward).parameters.keys())

    def forward(self, x):
        for k in list(x):
            if k not in self.hf_model_fwd_args:
                del x[k]

        return self.hf_model(
            **x,
            output_hidden_states=self.output_hidden_states,
            output_attentions=self.output_attentions,
            return_dict=True,
            **self.hf_model_kwargs
        )


# Cell
class HF_PreCalculatedLoss:
    def __call__(self, inp, targ, **kwargs):
        return tensor(0.0)

    def decodes(self, x):
        return x.argmax(dim=-1)

    def activation(self, x):
        return F.softmax(x, dim=-1)


# Cell
class HF_BaseModelCallback(Callback):
    def before_batch(self):
        self.hf_loss = None

    def after_pred(self):
        model_outputs = self.pred
        self.learn.blurr_model_outputs = {}

        for k, v in model_outputs.items():
            # if the "labels" are included, we are training with target labels in which case the loss is returned
            if k == "loss" and isinstance(self.learn.loss_func, HF_PreCalculatedLoss):
                self.hf_loss = to_float(v)
            # the logits represent the prediction
            elif k == "logits":
                self.learn.pred = v
            # add any other things included in model_outputs as blurr_{model_output_key}
            else:
                self.learn.blurr_model_outputs[k] = v

    def after_loss(self):
        # if we already have the loss from the model, update the Learner's loss to be it
        if self.hf_loss is not None:
            self.learn.loss_grad = self.hf_loss
            self.learn.loss = self.learn.loss_grad.clone()


# Cell
@typedispatch
def show_results(
    # This typedispatched `show_results` will be called for `HF_BaseInput` typed inputs
    x: HF_BaseInput,
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
    hf_tokenizer = tfm.hf_tokenizer

    # if we've included our labels list, we'll use it to look up the value of our target(s)
    trg_labels = tfm.kwargs["labels"] if ("labels" in tfm.kwargs) else None

    res = L()
    n_inp = learner.dls.n_inp

    for idx, (input_ids, label, pred, sample) in enumerate(zip(x, y, outs, samples)):
        if idx >= max_n:
            break

        # add in the input text
        rets = [hf_tokenizer.decode(input_ids, skip_special_tokens=True)[:trunc_at]]
        # add in the targets
        for item in sample[n_inp:]:
            if not torch.is_tensor(item):
                trg = trg_labels[int(item)] if trg_labels else item
            elif is_listy(item.tolist()):
                trg = [trg_labels[idx] for idx, val in enumerate(label.numpy().tolist()) if (val == 1)] if (trg_labels) else label.item()
            else:
                trg = trg_labels[label.item()] if (trg_labels) else label.item()

            rets.append(trg)
        # add in the predictions
        for item in pred:
            if not torch.is_tensor(item):
                p = trg_labels[int(item)] if trg_labels else item
            elif is_listy(item.tolist()):
                p = [trg_labels[idx] for idx, val in enumerate(item.numpy().tolist()) if (val == 1)] if (trg_labels) else item.item()
            else:
                p = trg_labels[item.item()] if (trg_labels) else item.item()

            rets.append(p)

        res.append(tuplify(rets))

    cols = ["text"] + ["target" if (i == 0) else f"target_{i}" for i in range(len(res[0]) - n_inp * 2)]
    cols += ["prediction" if (i == 0) else f"prediction_{i}" for i in range(len(res[0]) - n_inp * 2)]
    display_df(pd.DataFrame(res, columns=cols)[:max_n])
    return ctxs


# Cell
@patch
def blurr_generate(self: Learner, inp, **kwargs):
    """Uses the built-in `generate` method to generate the text
    (see [here](https://huggingface.co/transformers/main_classes/model.html#transformers.PreTrainedModel.generate)
    for a list of arguments you can pass in)
    """
    # grab our blurr tfm with the bits to properly decode/show our inputs/targets
    tfm = first_blurr_tfm(self.dls)

    # grab the Hugging Face tokenizer from the learner's dls.tfms
    hf_config = tfm.hf_config
    hf_tokenizer = tfm.hf_tokenizer
    tok_kwargs = tfm.tok_kwargs

    # grab the text generation kwargs
    text_gen_kwargs = tfm.text_gen_kwargs if (len(kwargs) == 0) else kwargs

    if isinstance(inp, str):
        input_ids = hf_tokenizer.encode(inp, padding=True, truncation=True, return_tensors="pt", **tok_kwargs)
    else:
        # note (10/30/2020): as of pytorch 1.7, this has to be a plain ol tensor (not a subclass of TensorBase)
        input_ids = inp.as_subclass(Tensor)

    input_ids = input_ids.to(self.model.hf_model.device)

    gen_texts = self.model.hf_model.generate(input_ids, **text_gen_kwargs)
    outputs = [hf_tokenizer.decode(txt, skip_special_tokens=True, clean_up_tokenization_spaces=False) for txt in gen_texts]

    if tfm.hf_arch == "pegasus":
        outputs = [o.replace("<n>", " ") for o in outputs]

    return outputs


# Cell
@delegates(Learner.__init__)
class Blearner(Learner):
    def __init__(
        self,
        # Your fast.ai DataLoaders
        dls: DataLoaders,
        # Your pretrained Hugging Face transformer
        hf_model: PreTrainedModel,
        # Your `HF_BaseModelCallback`
        base_model_cb: HF_BaseModelCallback = HF_BaseModelCallback,
        # Any kwargs you want to pass to your `BLearner`
        **kwargs
    ):
        model = kwargs.get("model", HF_BaseModelWrapper(hf_model))
        loss_func = kwargs.pop("loss_func", dls.loss_func if hasattr(dls, "loss_func") else None)
        splitter = kwargs.pop("splitter", hf_splitter)

        super().__init__(dls, model=model, loss_func=loss_func, splitter=splitter, **kwargs)

        self.add_cb(base_model_cb)
        self.freeze()


# Cell
@delegates(Blearner.__init__)
class BlearnerForSequenceClassification(Blearner):
    def __init__(self, dls: DataLoaders, hf_model: PreTrainedModel, **kwargs):
        super().__init__(dls, hf_model, **kwargs)

    @classmethod
    def get_model_cls(self):
        return AutoModelForSequenceClassification

    @classmethod
    def _get_x(cls, r, attr):
        return r[attr] if (isinstance(attr, str)) else tuple(r[inp] for inp in attr)

    @classmethod
    def _get_y(cls, r, attr):
        return r[attr] if (isinstance(attr, str)) else [r[inp] for inp in attr]

    @classmethod
    def _create_learner(
        cls,
        # Your raw dataset
        data,
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # A function to perform any preprocessing required for your Dataset
        preprocess_func: Callable = None,
        # The attribute in your dataset that contains your raw text
        text_attr: str = "text",
        # The attribute in your dataset that contains your labels/targets
        label_attr: str = "label",
        # The number of labels/classes your model should predict
        n_labels: int = 2,
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Callable = RandomSplitter(),
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs: dict = {},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs: dict = {},
    ):
        # get our hf objects
        hf_arch, hf_config, hf_tokenizer, hf_model = BLURR.get_hf_objects(
            pretrained_model_name_or_path, model_cls=cls.get_model_cls(), config_kwargs={"num_labels": n_labels}
        )

        # if we need to preprocess the raw data before creating our DataLoaders
        if preprocess_func:
            data = preprocess_func(data, hf_arch, hf_config, hf_tokenizer, hf_model, text_attr, label_attr)

        # not all architectures include a native pad_token (e.g., gpt2, ctrl, etc...), so we add one here
        if hf_tokenizer.pad_token is None:
            hf_tokenizer.add_special_tokens({"pad_token": "<pad>"})
            hf_config.pad_token_id = hf_tokenizer.get_vocab()["<pad>"]
            hf_model.resize_token_embeddings(len(hf_tokenizer))

        # defin our input/target getters
        if isinstance(data, pd.DataFrame):
            get_x = ColReader(text_attr)
            get_y = ColReader(label_attr)
        else:
            get_x = partial(cls._get_x, attr=text_attr)
            get_y = partial(cls._get_y, attr=label_attr)

        # infer loss function and default metrics
        if is_listy(label_attr):
            trg_block = MultiCategoryBlock(encoded=True, vocab=label_attr)
            learner_kwargs["metrics"] = learner_kwargs.get("metrics", [F1ScoreMulti(), accuracy_multi])
        else:
            trg_block = CategoryBlock
            learner_kwargs["metrics"] = learner_kwargs.get("metrics", [F1Score(), accuracy])

        # build our DataBlock and DataLoaders
        blocks = (HF_TextBlock(hf_arch, hf_config, hf_tokenizer, hf_model), trg_block)
        dblock = DataBlock(blocks=blocks, get_x=get_x, get_y=get_y, splitter=dblock_splitter)

        dls = dblock.dataloaders(data, **dl_kwargs.copy())

        # return BLearner instance
        return cls(dls, hf_model, **learner_kwargs.copy())

    @classmethod
    def from_dataframe(
        cls,
        # Your pandas DataFrame
        df: pd.DataFrame,
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # A function to perform any preprocessing required for your Dataset
        preprocess_func: Callable = None,
        # The attribute in your dataset that contains your raw text
        text_attr: str = "text",
        # The attribute in your dataset that contains your labels/targets
        label_attr: str = "label",
        # The number of labels/classes your model should predict
        n_labels: int = None,
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Callable = ColSplitter(),
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs: dict = {},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs: dict = {},
    ):
        # we need to tell transformer how many labels/classes to expect
        if n_labels is None:
            n_labels = len(label_attr) if (is_listy(label_attr)) else len(df[label_attr].unique())

        return cls._create_learner(
            df, pretrained_model_name_or_path, preprocess_func, text_attr, label_attr, n_labels, dblock_splitter, dl_kwargs, learner_kwargs
        )

    @classmethod
    def from_csv(
        cls,
        # The path to your csv file
        csv_file: Union[Path, str],
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # A function to perform any preprocessing required for your Dataset
        preprocess_func: Callable = None,
        # The attribute in your dataset that contains your raw text
        text_attr: str = "text",
        # The attribute in your dataset that contains your labels/targets
        label_attr: str = "label",
        # The number of labels/classes your model should predict
        n_labels: int = None,
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Callable = ColSplitter(),
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs={},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs={},
    ):
        df = pd.read_csv(csv_file)

        return cls.from_dataframe(
            df,
            pretrained_model_name_or_path=pretrained_model_name_or_path,
            preprocess_func=preprocess_func,
            text_attr=text_attr,
            label_attr=label_attr,
            n_labels=n_labels,
            dblock_splitter=dblock_splitter,
            dl_kwargs=dl_kwargs,
            learner_kwargs=learner_kwargs,
        )

    @classmethod
    def from_dictionaries(
        cls,
        # A list of dictionaries
        ds: List[Dict],
        # The name or path of the pretrained model you want to fine-tune
        pretrained_model_name_or_path: Optional[Union[str, os.PathLike]],
        # A function to perform any preprocessing required for your Dataset
        preprocess_func: Callable = None,
        # The attribute in your dataset that contains your raw text
        text_attr: str = "text",
        # The attribute in your dataset that contains your labels/targets
        label_attr: str = "label",
        # The number of labels/classes your model should predict
        n_labels: int = None,
        # A function that will split your Dataset into a training and validation set
        # See [here](https://docs.fast.ai/data.transforms.html#Split) for a list of fast.ai splitters
        dblock_splitter: Callable = RandomSplitter(),
        # Any kwargs to pass to your `DataLoaders`
        dl_kwargs: dict = {},
        # Any kwargs to pass to your task specific `Blearner`
        learner_kwargs: dict = {},
    ):
        # we need to tell transformer how many labels/classes to expect
        if n_labels is None:
            n_labels = len(label_attr) if (is_listy(label_attr)) else len(set([item[label_attr] for item in ds]))

        return cls._create_learner(
            ds, pretrained_model_name_or_path, preprocess_func, text_attr, label_attr, n_labels, dblock_splitter, dl_kwargs, learner_kwargs
        )
