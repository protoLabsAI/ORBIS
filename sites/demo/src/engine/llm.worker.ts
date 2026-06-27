/// <reference lib="webworker" />
/**
 * On-device Gemma worker. Loads google/gemma-4-E2B (ONNX, q4f16) on WebGPU
 * via Transformers.js and streams tokens back. Runs off the main thread so
 * model load + generation never stall the orb's render loop.
 *
 * Protocol (main → worker): { type: 'load' } | { type: 'generate', data: messages } | { type: 'interrupt' }
 * Protocol (worker → main): 'progress' | 'ready' | 'start' | 'token' | 'done' | 'error'
 */
import {
  AutoTokenizer,
  AutoModelForCausalLM,
  TextStreamer,
  InterruptableStoppingCriteria,
  env,
  type PreTrainedTokenizer,
  type PreTrainedModel,
} from '@huggingface/transformers';

// Persist model files in the browser Cache API so a full download only
// happens once; always resolve from the HF hub.
env.useBrowserCache = true;
env.allowLocalModels = false;

const MODEL_ID = 'onnx-community/gemma-4-E2B-it-ONNX';

let tokenizer: PreTrainedTokenizer | null = null;
let model: PreTrainedModel | null = null;
const stopper = new InterruptableStoppingCriteria();

const post = (msg: Record<string, unknown>) => self.postMessage(msg);

async function load(): Promise<void> {
  const progress_callback = (p: unknown) => post({ type: 'progress', data: p });
  tokenizer ??= await AutoTokenizer.from_pretrained(MODEL_ID, { progress_callback });
  model ??= await AutoModelForCausalLM.from_pretrained(MODEL_ID, {
    dtype: 'q4f16',
    device: 'webgpu',
    progress_callback,
  });
  // Tiny warm-up compile so the first real turn isn't cold.
  const warm = tokenizer!.apply_chat_template([{ role: 'user', content: 'hi' }], {
    add_generation_prompt: true,
    return_dict: true,
  });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await model!.generate({ ...(warm as any), max_new_tokens: 1 });
  post({ type: 'ready' });
}

async function generate(messages: Array<{ role: string; content: string }>): Promise<void> {
  if (!tokenizer || !model) throw new Error('model not loaded');
  stopper.reset();
  const inputs = tokenizer.apply_chat_template(messages, {
    add_generation_prompt: true,
    return_dict: true,
  });
  const streamer = new TextStreamer(tokenizer, {
    skip_prompt: true,
    skip_special_tokens: true,
    callback_function: (text: string) => post({ type: 'token', data: text }),
  });
  post({ type: 'start' });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await model.generate({
    ...(inputs as any),
    max_new_tokens: 384,
    do_sample: false,
    streamer,
    stopping_criteria: stopper,
  });
  post({ type: 'done' });
}

self.addEventListener('message', async (e: MessageEvent) => {
  const { type, data } = e.data ?? {};
  try {
    if (type === 'load') await load();
    else if (type === 'generate') await generate(data);
    else if (type === 'interrupt') stopper.interrupt();
  } catch (err) {
    post({ type: 'error', data: String((err as Error)?.message ?? err) });
  }
});
