# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/01c_data-question-answering.ipynb (unless otherwise specified).

__all__ = ['pre_process_squad', 'HF_QuestionAnswerInput']

# Cell
import ast
from functools import reduce

from ..utils import *
from .core import *

import torch
from transformers import *
from fastai2.text.all import *

# Cell
def pre_process_squad(row, hf_arch, hf_tokenizer):
    context, qst, ans = row['context'], row['question'], row['answer_text']

    add_prefix_space = hf_arch in ['gpt2', 'roberta']

    if(hf_tokenizer.padding_side == 'right'):
        tok_input = hf_tokenizer.convert_ids_to_tokens(hf_tokenizer.encode(qst, context,
                                                                           add_prefix_space=add_prefix_space))
    else:
        tok_input = hf_tokenizer.convert_ids_to_tokens(hf_tokenizer.encode(context, qst,
                                                                           add_prefix_space=add_prefix_space))

    tok_ans = hf_tokenizer.tokenize(str(row['answer_text']),
                                    add_special_tokens=False,
                                    add_prefix_space=add_prefix_space)

    start_idx, end_idx = 0,0
    for idx, tok in enumerate(tok_input):
        try:
            if (tok == tok_ans[0] and tok_input[idx:idx + len(tok_ans)] == tok_ans):
                start_idx, end_idx = idx, idx + len(tok_ans)
                break
        except: pass

    row['tokenized_input'] = tok_input
    row['tokenized_input_len'] = len(tok_input)
    row['tok_answer_start'] = start_idx
    row['tok_answer_end'] = end_idx

    return row

# Cell
class HF_QuestionAnswerInput(list): pass

# Cell
@typedispatch
def build_hf_input(task:QuestionAnsweringTask, tokenizer, a_tok_ids, b_tok_ids=None, targets=None,
                   max_length=512, pad_to_max_length=True, truncation_strategy=None):

    if (truncation_strategy is None):
        truncation_strategy = "only_second" if tokenizer.padding_side == "right" else "only_first"

    res = tokenizer.prepare_for_model(a_tok_ids if tokenizer.padding_side == "right" else b_tok_ids,
                                      b_tok_ids if tokenizer.padding_side == "right" else a_tok_ids,
                                      max_length=max_length,
                                      pad_to_max_length=pad_to_max_length,
                                      truncation_strategy=truncation_strategy,
                                      return_special_tokens_mask=True,
                                      return_tensors='pt')

    input_ids = res['input_ids'][0]
    attention_mask = res['attention_mask'][0] if ('attention_mask' in res) else tensor([-9999])
    token_type_ids = res['token_type_ids'][0] if ('token_type_ids' in res) else tensor([-9999])

    # cls_index: location of CLS token (used by xlnet and xlm) ... this is a list.index(value) for pytorch tensor's
    cls_index = (input_ids == tokenizer.cls_token_id).nonzero()[0]

    # p_mask: mask with 1 for token than cannot be in the answer, else 0 (used by xlnet and xlm)
    p_mask = tensor(res['special_tokens_mask']) if ('special_tokens_mask' in res) else tensor([-9999])

    return HF_QuestionAnswerInput([input_ids, attention_mask, token_type_ids, cls_index, p_mask]), targets

# Cell
@typedispatch
def show_batch(x:HF_QuestionAnswerInput, y, samples, hf_tokenizer, skip_special_tokens=True,
               ctxs=None, max_n=6, **kwargs):
    res = L()
    for inp, start, end in zip(x[0], *y):
        txt = hf_tokenizer.decode(inp, skip_special_tokens=skip_special_tokens).replace(hf_tokenizer.pad_token, '')
        ans_toks = hf_tokenizer.convert_ids_to_tokens(inp, skip_special_tokens=False)[start:end]
        res.append((txt, (start.item(),end.item()), hf_tokenizer.convert_tokens_to_string(ans_toks)))

    display_df(pd.DataFrame(res, columns=['text', 'start/end', 'answer'])[:max_n])
    return ctxs