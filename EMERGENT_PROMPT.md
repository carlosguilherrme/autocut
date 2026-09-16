# Prompt para colar no Emergent

> Repositório público já no ar em `https://github.com/carlosguilherrme/autocut`. Depois de o app subir, cadastre `GROQ_API_KEY` (ou `OPENAI_API_KEY`) nas variáveis de ambiente do backend.

---

Quero um app web chamado **AutoCut**: eu envio um vídeo (vlog do YouTube ou Reel do Instagram) e ele volta editado automaticamente do jeito que eu gosto, sem eu abrir o CapCut. O motor de edição **já existe e está pronto** em Python; o seu trabalho é integrar, expor a API e fazer a interface. Não reescreva o motor.

## 1. Motor pronto (usar como está)

Repositório: `https://github.com/carlosguilherrme/autocut`

Instale no backend com:

```
pip install "git+https://github.com/carlosguilherrme/autocut.git"
```

(ou copie a pasta `autocut/` do repositório para `/app/backend/autocut/`). Dependências dele: `imageio-ffmpeg` (traz um binário ffmpeg com libass, não precisa de `apt-get`) e `requests`. **Não instale `faster-whisper` nem `torch`** neste ambiente — a transcrição aqui é por API.

Interface Python:

```python
from autocut import pipeline
from autocut.plan import EditPlan
from autocut.transcribe import Transcript

# processamento completo (bloqueante, rodar em thread de background)
pipeline.run(input_path, output_mp4, preset="auto", overrides={}, workdir=work_dir,
             progress=lambda stage, frac: ...)   # stage: probing|transcribing|planning|rendering; frac 0..1

# re-render de um plano editado pelo usuário (não transcreve de novo)
plan = EditPlan.load(f"{work_dir}/plan.json")
plan.segments[i].speed = 1.0            # ou .enabled = False
plan.save(f"{work_dir}/plan.json")
pipeline.render_plan(plan, output_mp4, work_dir, transcript=Transcript.load(f"{work_dir}/transcript.json"), progress=...)
```

`workdir` fica com `plan.json` (trechos, velocidades, legendas, stats), `transcript.json` e `subs.ass`; o `.srt` sai ao lado do mp4.

O visual que o motor produz (já é o padrão, não mexer): legenda **uma palavra grande de cada vez** (Poppins Bold, minúscula, branca, ~77 % da altura), vídeo em **preto e branco** com flashes coloridos curtos, **cards de título em serifa com sublinhado** nas ideias-chave (escolhidas por LLM) com **B-roll em tela cheia** (P&B, zoom lento), cortes nas pausas, 1,2x nos blocos longos, punch-in entre cortes, fade pro preto no fim.

Presets: `auto` (escolhe 9:16 ou 16:9 pelo vídeo), `reels`, `youtube`, `reels-fast`, `youtube-fast`. Overrides aceitos (chaves de `autocut/presets.py`): `speed` (1.2), `speed_mode` (`long`|`all`|`none`), `long_threshold` (6.0), `min_silence` (0.45), `caption_mode` (`word`|`phrase`), `grade` (`bw`|`none`), `color_pops`, `titles`, `titles_max` (4), `broll` (`auto`|`openai`|`fal`|`pexels`|`openverse`|`none`), `fade_out` (0.6), `subtitle_style` (`classic`|`box`|`yellow`, só no modo phrase), `fit` (`blur`|`crop`|`pad`), `aspect`, `punch_in`, `normalize_audio`, `subtitles`, `language` (`pt`).

Variáveis de ambiente do backend:

- Transcrição: `GROQ_API_KEY` (modelo `whisper-large-v3-turbo`, endpoint `https://api.groq.com/openai/v1`) ou `OPENAI_API_KEY` (`whisper-1`). Opcionais: `TRANSCRIBE_BASE_URL`, `TRANSCRIBE_MODEL`, `TRANSCRIBE_API_KEY`.
- Ideias-chave dos cards: `ANTHROPIC_API_KEY` (claude-haiku-4-5) ou `OPENAI_API_KEY`/`GROQ_API_KEY` (chat). Sem chave, cai numa heurística.
- Imagens de B-roll: `OPENAI_API_KEY` (gpt-image-1, melhor fidelidade ao estilo) ou `FAL_KEY` (flux/schnell) ou `PEXELS_API_KEY` (banco de fotos). Sem nenhuma, usa Openverse (fotos CC, qualidade irregular).
- `AUTOCUT_DATA=/app/backend/data` (pasta dos jobs em disco).

## 2. Backend (FastAPI)

Há uma implementação de referência completa em `app/backend/server.py` do repositório — copie e adapte. Requisitos:

- `POST /api/jobs` multipart (`file`, `preset`, `overrides` como JSON string). Salvar o upload em streaming por chunks (vídeos de até 2 GB), criar o job e **processar em thread de background** (`ThreadPoolExecutor` com 1 worker, jobs em fila). A request responde na hora com o `id`.
- `GET /api/jobs/{id}` → `status` (`queued|transcribing|planning|rendering|done|error`), `stage`, `progress` (0–1), `stats`, `plan`, `error`, `download_url`, `srt_url`.
- `PUT /api/jobs/{id}/plan` → `{ "segments": [{"index": 3, "speed": 1.0, "enabled": true}], "settings": {"subtitle_style": "box"} }`.
- `POST /api/jobs/{id}/render` → re-renderiza o plano editado.
- `GET /api/jobs/{id}/download` (mp4 com `Content-Disposition`), `/srt`, `/preview` (para o player), `GET /api/jobs`, `DELETE /api/jobs/{id}`, `GET /api/presets`, `GET /api/health` (mostra qual ffmpeg foi achado).
- Metadados do job no MongoDB (coleção `jobs`, campos iguais ao `job.json` da referência); arquivos em disco em `AUTOCUT_DATA/jobs/{id}/`. Ao subir o servidor, jobs que ficaram em processamento viram `error`.
- Nunca bloquear o event loop com ffmpeg/transcrição.

## 3. Frontend (React + Tailwind), tudo em português, mobile-first (vou usar pelo celular)

Tela única, fundo escuro, acento roxo, sem enfeite:

1. **Envio**: dropzone grande (arrastar ou tocar para escolher), seletor de preset (Auto / Reels 9:16 / YouTube 16:9) e um "Avançado" recolhido com: velocidade (1,0–2,0, padrão 1,2), acelerar (só trechos longos / tudo / nada), corte de silêncio em segundos (padrão 0,45), legenda (palavra grande / frase embaixo), preto e branco (liga/desliga), cards de título (liga/desliga, máximo 4), B-roll (auto / sem imagem). Botão **Editar**.
2. **Progresso**: card do job com barra e etapa em português (transcrevendo → cortando → renderizando), atualizando por polling a cada 1,5 s. Mostrar erro legível se falhar.
3. **Resultado**: player com o vídeo final, números (cortes, segundos removidos, duração final, trechos acelerados, legendas) e botões **Baixar MP4** e **Baixar SRT**.
4. **Ajuste fino**: tabela dos trechos (tempo de origem, duração, chips 1x / 1,2x / 1,5x, checkbox manter) e botão **Re-renderizar**. As legendas continuam sincronizadas sozinhas.
5. **Histórico**: últimos 20 jobs com status e re-download.

## 4. Testes de aceite

- Enviar um `.mov` vertical de 1 min falado em português → volta 9:16, em preto e branco, com legenda palavra por palavra, cards de título com imagem, cortes nas pausas e 1,2x nos blocos longos; o `.srt` também baixa.
- Enviar um `.mp4` 16:9 → volta 16:9.
- Trocar a velocidade de um trecho para 1x e re-renderizar → legendas seguem no lugar certo.
- Sem chave de transcrição → job termina em `error` com mensagem clara (não trava a fila).
- `GET /api/health` mostra um caminho de ffmpeg (do `imageio-ffmpeg`).

## 5. Limites conhecidos

- ffmpeg roda no mesmo container: vídeo de 20 min pode levar vários minutos; mostrar progresso real (a função `progress` já dá isso).
- Se uploads acima de ~100 MB falharem no proxy do Emergent, me avise antes de implementar upload em partes.
