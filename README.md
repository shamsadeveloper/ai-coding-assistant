# ai-coding-assistant
# LLM Learning

LLM development seekhne ke practice scripts (Ollama ke saath, locally).

## Requirements
- Python 3.12+, Ollama
- `ollama pull llama3.2` aur `ollama pull nomic-embed-text`
- `pip install ollama numpy`

## Scripts
| File | Concept |
|---|---|
| neuron.py | Ek neuron: weights, bias, ReLU |
| gradient_descent.py | Loss aur gradient se weight update |
| token_practice.py, tokenization_practice.py | Characters vs words (tokens ka intro) |
| chatbots.py | Ollama se ek single sawal |
| chatbot.py | Memory wala chat loop (`exit` se band) |
| context_practice.py | Context window kaise barhti hai |
| embedding_practice.py | Embeddings + cosine similarity |

## Projects
- [ai-coding-assistant](ai-coding-assistant/) — local AI coding assistant (FastAPI + React + Ollama)
