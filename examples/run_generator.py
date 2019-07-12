#!/usr/bin/env python3

import argparse
import logging
from tqdm import trange

import torch
import torch.nn.functional as F
import numpy as np

from pytorch_transformers import GPT2Config, OpenAIGPTConfig, XLNetConfig

from pytorch_transformers import GPT2LMHeadModel, GPT2Tokenizer
from pytorch_transformers import OpenAIGPTLMHeadModel, OpenAIGPTTokenizer
from pytorch_transformers import XLNetLMHeadModel, XLNetTokenizer

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)

ALL_MODELS = sum((tuple(conf.pretrained_config_archive_map.keys()) for conf in (GPT2Config, OpenAIGPTConfig, XLNetConfig)), ())

MODEL_CLASSES = {
    'gpt2': (GPT2Config, GPT2LMHeadModel, GPT2Tokenizer),
    'gpt': (OpenAIGPTConfig, OpenAIGPTLMHeadModel, OpenAIGPTTokenizer),
    'xlnet': (XLNetConfig, XLNetLMHeadModel, XLNetTokenizer),
}

def top_k_logits(logits, k):
    """
    Masks everything but the k top entries as -infinity (1e10).
    Used to mask logits such that e^-infinity -> 0 won't contribute to the
    sum of the denominator.
    """
    if k == 0:
        return logits
    else:
        values = torch.topk(logits, k)[0]
        batch_mins = values[:, -1].view(-1, 1).expand_as(logits)
        return torch.where(logits < batch_mins, torch.ones_like(logits) * -1e10, logits)

def sample_sequence(model, length, model_class, start_token=None, batch_size=None, context=None, temperature=1, top_k=0, device='cuda', sample=True):
    if start_token is None:
        assert context is not None, 'Specify exactly one of start_token and context!'
        context = torch.tensor(context, device=device, dtype=torch.long).unsqueeze(0).repeat(batch_size, 1)
    else:
        assert context is None, 'Specify exactly one of start_token and context!'
        context = torch.full((batch_size, 1), start_token, device=device, dtype=torch.long)
    prev = context
    output = context
    past = None
    with torch.no_grad():
        for i in trange(length):
            if model_class == "gpt2":
                logits, past = model(prev, past=past)
            else:
                logits = model(prev)[0]

            logits = logits[:, -1, :] / temperature
            logits = top_k_logits(logits, k=top_k)
            log_probs = F.softmax(logits, dim=-1)
            if sample:
                prev = torch.multinomial(log_probs, num_samples=1)
            else:
                _, prev = torch.topk(log_probs, k=1, dim=-1)
            output = torch.cat((output, prev), dim=1)
    return output

def run_model():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model_name', type=str, default=None, required=True,
                        help="Bert/XLNet/XLM pre-trained model selected in the list: " + ", ".join(ALL_MODELS))
    parser.add_argument('--pretrained_checkpoint_name_or_path', type=str, default=None, required=True,
                        help='pretrained model name or path to local checkpoint')
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--nsamples", type=int, default=1)
    parser.add_argument("--batch_size", type=int, default=-1)
    parser.add_argument("--length", type=int, default=-1)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top_k", type=int, default=0)
    parser.add_argument('--unconditional', action='store_true', help='If true, unconditional generation.')
    args = parser.parse_args()
    print(args)

    if args.batch_size == -1:
        args.batch_size = 1
    assert args.nsamples % args.batch_size == 0

    np.random.seed(args.seed)
    torch.random.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    args.model_type = ""
    for key in MODEL_CLASSES:
        if key in args.model_name.lower():
            args.model_type = key  # take the first match in model types
            break

    config_class, model_class, tokenizer_class = MODEL_CLASSES[args.model_type]

    enc = tokenizer_class.from_pretrained(args.pretrained_checkpoint_name_or_path)
    model = model_class.from_pretrained(args.pretrained_checkpoint_name_or_path)
    model.to(device)
    model.eval()

    if args.length == -1:
        args.length = model.config.max_position_embeddings if args.model_type == "xlnet" else model.config.n_ctx // 2
    elif args.length > model.config.n_ctx:
        raise ValueError("Can't get samples longer than window size: %s" % model.config.n_ctx)

    while True:
        context_tokens = []
        if not args.unconditional:
            raw_text = input("Model prompt >>> ")
            while not raw_text:
                print('Prompt should not be empty!')
                raw_text = input("Model prompt >>> ")
            context_tokens = enc.encode(raw_text)
            generated = 0
            for _ in range(args.nsamples // args.batch_size):
                out = sample_sequence(
                    model=model, length=args.length,
                    model_class=args.model_type,
                    context=context_tokens,
                    start_token=None,
                    batch_size=args.batch_size,
                    temperature=args.temperature, top_k=args.top_k, device=device
                )
                out = out[:, len(context_tokens):].tolist()
                for i in range(args.batch_size):
                    generated += 1
                    text = enc.decode(out[i])
                    print("=" * 40 + " SAMPLE " + str(generated) + " " + "=" * 40)
                    print(text)
            print("=" * 80)
        else:
            generated = 0
            for _ in range(args.nsamples // args.batch_size):
                out = sample_sequence(
                    model=model, length=args.length,
                    model_class=args.model_type,
                    context=None,
                    start_token=enc.encoder['<|endoftext|>'],
                    batch_size=args.batch_size,
                    temperature=args.temperature, top_k=args.top_k, device=device
                )
                out = out[:,1:].tolist()
                for i in range(args.batch_size):
                    generated += 1
                    text = enc.decode(out[i])
                    print("=" * 40 + " SAMPLE " + str(generated) + " " + "=" * 40)
                    print(text)
            print("=" * 80)

if __name__ == '__main__':
    run_model()


