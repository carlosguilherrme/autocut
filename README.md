# AutoCut

Editor automático de vlogs e Reels. Manda o vídeo, volta editado do jeito combinado:

- **Corte de silêncio**: toda pausa acima de 0,45 s vira um jump cut (com respiro de 0,10 s antes e 0,18 s depois da fala).
- **1,2x nos trechos longos**: cada bloco de fala com 6 s ou mais acelera para 1,2x; blocos curtos ficam no 1x. Modo `all` acelera tudo, `none` não acelera.
- **Legenda queimada**: frase por linha, 1–2 linhas embaixo, Poppins Bold branca com contorno preto (estilos `classic`, `box`, `yellow`). Também gera um `.srt` separado.
- **Formato**: `auto` escolhe 9:16 ou 16:9 pelo vídeo de origem. Quando o formato muda, usa fundo desfocado (`blur`), ou `crop`/`pad`.
- **Reels**: punch-in leve alternado entre os cortes. **Áudio**: normalizado em -16 LUFS.

Tudo é um plano em JSON (`plan.json`) que pode ser editado (velocidade por trecho, ligar/desligar trecho) e re-renderizado sem transcrever de novo.

## Como funciona

```
vídeo → áudio 16 kHz → Whisper (palavras com tempo) → regiões de fala (gaps + VAD Silero)
      → plano: trechos mantidos + velocidade + zoom → legendas mapeadas na linha do tempo final
      → ffmpeg (1 passada): trim/setpts/atempo por trecho → concat → fit 9:16/16:9 → subtitles (libass) → H.264
```

Arquivos: `autocut/transcribe.py` (Whisper local ou API), `speech.py` (regiões de fala), `plan.py` (cortes, velocidade, cues), `subtitles.py` (ASS/SRT), `render.py` (filter_complex do ffmpeg), `presets.py` (os defaults "como eu gosto"), `pipeline.py` (orquestra), `cli.py`.

## Instalar (Mac)

```bash
cd ~/Desktop/autocut && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[local,server]"
```

`imageio-ffmpeg` traz um ffmpeg com libass (o ffmpeg do Homebrew nesta máquina veio sem libass, por isso o pacote ignora ele). `faster-whisper` roda o modelo `large-v3-turbo` na CPU (~11 s por minuto de áudio no M5 Pro; o download do modelo, 1,6 GB, acontece só na primeira vez).

## Usar (linha de comando)

```bash
.venv/bin/python -m autocut ~/Desktop/videos/vlog.mov
```

Sai `vlog_autocut.mp4` + `vlog_autocut.srt` ao lado do original e uma pasta `vlog_autocut_work/` com `plan.json`, `transcript.json`, `subs.ass`.

Opções úteis:

```bash
--preset reels|youtube|reels-fast|youtube-fast|auto
--speed 1.25 --speed-mode all|long|none --long-threshold 6
--min-silence 0.6            # pausa mínima para cortar
--style classic|box|yellow --uppercase --no-subs
--fit blur|crop|pad --aspect 9:16|16:9|1:1
--punch-in / --no-punch-in --no-normalize
--backend local|api --model large-v3-turbo --language pt
--plan-only                  # só transcreve e planeja
--from-plan work/plan.json   # renderiza um plano editado à mão
```

## API local (mesma que vai para o Emergent)

```bash
.venv/bin/python -m uvicorn app.backend.server:app --port 8001
```

Abre `http://localhost:8001/` (UI mínima em `app/frontend/index.html`). Endpoints:

| Método | Rota | Função |
|---|---|---|
| POST | `/api/jobs` (multipart `file`, `preset`, `overrides` JSON) | cria job e processa em background |
| GET | `/api/jobs/{id}` | status, `progress` 0–1, `stage`, `stats`, `plan` |
| PUT | `/api/jobs/{id}/plan` | edita velocidade/enabled por trecho e estilo de legenda |
| POST | `/api/jobs/{id}/render` | re-renderiza o plano editado (sem transcrever de novo) |
| GET | `/api/jobs/{id}/download` · `/srt` · `/preview` | arquivos finais |
| GET | `/api/jobs` · DELETE `/api/jobs/{id}` | histórico |
| GET | `/api/presets` · `/api/health` | presets e checagem do ffmpeg |

Variáveis: `AUTOCUT_DATA` (pasta dos jobs), `AUTOCUT_MAX_UPLOAD_MB`, `AUTOCUT_WORKERS`.

## Transcrição por API (sem GPU/CPU pesada, é o modo do Emergent)

```bash
export GROQ_API_KEY=...            # whisper-large-v3-turbo, rápido e barato
# ou export OPENAI_API_KEY=...     # whisper-1
# ou TRANSCRIBE_BASE_URL / TRANSCRIBE_MODEL / TRANSCRIBE_API_KEY para qualquer endpoint compatível
.venv/bin/python -m autocut vlog.mov --backend api
```

Áudio acima de 20 MB é fatiado em blocos de 10 min automaticamente.

## Levar para o Emergent

Ver [EMERGENT_PROMPT.md](EMERGENT_PROMPT.md): é o prompt pronto para colar no Emergent, apontando para este repositório no GitHub. O agente instala o pacote com `pip`, copia `app/backend/server.py` e monta o front em React.
