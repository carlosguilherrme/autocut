# AutoCut

Editor automático de vlogs e Reels. Manda o vídeo, volta editado no estilo dos seus Reels:

- **Legenda palavra por palavra**: uma palavra grande de cada vez (Poppins Bold, minúscula, branca, a ~77 % da altura), com um "pop" sutil de entrada. Modo alternativo `phrase`: frase por linha, 1–2 linhas embaixo.
- **Preto e branco** com contraste leve, e flashes coloridos curtos (abertura e antes de cada card). `--no-bw` mantém a cor.
- **Cards de título em serifa** (DM Serif Display, sublinhado fino) nas ideias-chave da fala, escolhidas por um LLM, com **B-roll em tela cheia** (P&B, vinheta, zoom lento) quando há um provedor de imagem. Sem imagem, o card entra sobre a própria filmagem.
- **Corte de silêncio**: pausa acima de 0,45 s vira jump cut (respiro de 0,10 s antes e 0,18 s depois).
- **1,2x nos trechos longos**: blocos de fala com 6 s ou mais aceleram; `--speed-mode all` acelera tudo.
- **Punch-in** alternado entre cortes (preset `reels`), **fade pro preto** no fim, **áudio** normalizado em -16 LUFS, `.srt` separado.
- **Formato**: `auto` escolhe 9:16 ou 16:9 pelo vídeo; quando muda, fundo desfocado (`blur`), ou `crop`/`pad`.

Tudo vira um plano em JSON (`plan.json`): trechos, velocidades, legendas, cards. Dá pra editar (velocidade por trecho, ligar/desligar) e re-renderizar sem transcrever de novo; legendas e cards se realinham sozinhos.

## Como funciona

```
vídeo → áudio 16 kHz → Whisper (palavras com tempo) → regiões de fala (gaps + VAD Silero)
      → plano: trechos + velocidade + zoom → legendas-palavra na linha do tempo final
      → LLM escolhe 3–4 expressões-chave → cards serifados + imagem (OpenAI / fal / Pexels / Openverse / pasta local)
      → ffmpeg (1 passada): trim/setpts/atempo → concat → fit → P&B → overlays de B-roll → libass → fade → H.264
```

Arquivos: `autocut/transcribe.py` (Whisper local ou API), `speech.py` (fala/silêncio), `plan.py` (cortes, velocidade, cues, cards), `keywords.py` (escolha das ideias-chave), `broll.py` (imagens), `subtitles.py` (ASS/SRT), `render.py` (filter_complex), `presets.py` (defaults), `pipeline.py`, `cli.py`.

## Instalar (Mac)

```bash
cd ~/Desktop/autocut && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[local,server]"
```

`imageio-ffmpeg` traz um ffmpeg com libass (o do Homebrew nesta máquina veio sem). `faster-whisper` roda `large-v3-turbo` na CPU (~11 s por minuto de áudio no M5 Pro; download de 1,6 GB só na primeira vez). A escolha das ideias-chave usa, nesta ordem: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`/`GROQ_API_KEY`, o `claude` CLI logado no Mac, ou uma heurística sem rede.

## Usar

```bash
.venv/bin/autocut ~/Desktop/videos/vlog.mov --preset reels
```

Sai `vlog_autocut.mp4` + `.srt` ao lado do original e `vlog_autocut_work/` com `plan.json`, `transcript.json`, `subs.ass`, `broll/`.

Opções:

```bash
--preset reels|youtube|reels-fast|youtube-fast|auto
--captions word|phrase          # palavra grande (padrão) ou frase embaixo
--no-bw --no-color-pops         # cor original / sem flashes
--no-titles --titles-max 4      # cards serifados
--keywords auto|anthropic|openai|claude-cli|heuristic
--broll auto|openai|fal|pexels|openverse|local|none --broll-dir ./imgs
--speed 1.25 --speed-mode all|long|none --long-threshold 6
--min-silence 0.6 --fit blur|crop|pad --aspect 9:16|16:9|1:1
--punch-in / --no-punch-in --fade-out 0.6 --no-normalize
--style classic|box|yellow --uppercase   # só no modo phrase
--backend local|api --model large-v3-turbo --language pt
--plan-only / --from-plan work/plan.json
```

### Imagens de B-roll (por chave de ambiente)

| Provedor | Chave | Resultado |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | gera a imagem pelo prompt (gpt-image-1), mais fiel ao estilo dos Reels |
| `fal` | `FAL_KEY` | gera com flux/schnell, rápido e barato |
| `pexels` | `PEXELS_API_KEY` | foto de banco por palavra-chave (grátis) |
| `openverse` | nenhuma | fotos Creative Commons; qualidade irregular, é o fallback |
| `local` | pasta `1.jpg, 2.jpg…` | suas próprias imagens, uma por card |

## API local (mesma que vai para o Emergent)

```bash
.venv/bin/python -m uvicorn app.backend.server:app --port 8001
```

Abre `http://localhost:8001/` (UI mínima em `app/frontend/index.html`). Endpoints:

| Método | Rota | Função |
|---|---|---|
| POST | `/api/jobs` (multipart `file`, `preset`, `overrides` JSON) | cria job e processa em background |
| GET | `/api/jobs/{id}` | status, `progress` 0–1, `stage`, `stats`, `plan` |
| PUT | `/api/jobs/{id}/plan` | edita velocidade/enabled por trecho e opções de visual |
| POST | `/api/jobs/{id}/render` | re-renderiza o plano editado |
| GET | `/api/jobs/{id}/download` · `/srt` · `/preview` | arquivos finais |
| GET | `/api/jobs` · DELETE `/api/jobs/{id}` | histórico |
| GET | `/api/presets` · `/api/health` | presets e checagem do ffmpeg |

Variáveis: `AUTOCUT_DATA`, `AUTOCUT_MAX_UPLOAD_MB`, `AUTOCUT_WORKERS`, mais as chaves acima.

## Transcrição por API (modo do Emergent)

```bash
export GROQ_API_KEY=...            # whisper-large-v3-turbo, rápido e barato
# ou OPENAI_API_KEY (whisper-1), ou TRANSCRIBE_BASE_URL / TRANSCRIBE_MODEL / TRANSCRIBE_API_KEY
.venv/bin/autocut vlog.mov --backend api
```

Áudio acima de 20 MB é fatiado em blocos de 10 min.

## Levar para o Emergent

Ver [EMERGENT_PROMPT.md](EMERGENT_PROMPT.md).
