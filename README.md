# AIGC Studio

A local, provider-agnostic workbench for generating commercial images and
short videos. It includes a FastAPI backend, a Vite/React web UI, provider
adapters, reusable generation skills, quality checks, and a local asset
uploader.

The project keeps provider credentials on the server and writes generated
media to local output folders. It does not include API keys, customer assets,
or a hosted generation service.

## Download and install

On Windows, clone the repository and double-click `start-aigc.bat`. The
launcher installs Python 3.11+ and Node.js 18+ when they are missing, creates
a project virtual environment, installs Python and npm packages, builds the
UI, and starts Studio.

```powershell
git clone https://github.com/mocqs1/AIGC.git
cd AIGC
Copy-Item .env.example .env
.\start-aigc.bat
```

Runtime bootstrap order:

1. Reuse a usable Python 3.11+ or Node.js 18+ already on PATH.
2. Install missing runtimes with `winget` (`Python.Python.3.12`, `OpenJS.NodeJS.LTS`).
3. If `winget` is unavailable, download the official Python 3.12 and Node.js LTS installers.

The Node.js MSI fallback may prompt for administrator approval. After a brand-new
runtime install, if PATH is not refreshed in the current window, close it and
run `start-aigc.bat` again.

Manual setup is still available:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
cd web
npm install
npm run build
cd ..
```

On macOS or Linux, activate the virtual environment with
`source .venv/bin/activate`. Automatic runtime installation is Windows-only.

Requirements: Python 3.11+, Node.js 18+, npm, and a supported provider API
key for the workflow you want to run.

## Configure providers

Create a local configuration file from the safe template:

```powershell
Copy-Item .env.example .env
```

Configure at least the provider used by your workflow. For example:

```dotenv
HERMES_API_URL=https://your-image-gateway.example/v1
HERMES_API_KEY=replace-with-your-key
HERMES_MODEL=gpt-image-2
VEO_API_KEY=replace-with-your-key
SEEDANCE_API_KEY=replace-with-your-key
```

Use `.env.example` for the complete endpoint, model, timeout, and optional R2
settings. Never commit `.env`; it is ignored by Git.

## Run the web studio

On Windows, double-click `start-aigc.bat` or run:

```powershell
.\start-aigc.bat
```

The launcher checks Python and Node.js, installs missing runtimes and
dependencies, builds the UI, starts the local API, and opens the studio. Each
launch picks a random free high port in 49152-65535, skipping common service
ports such as 8000, 8080, 3000, and 5173. The chosen URL is printed and opened
automatically.

Stop the managed server with:

```powershell
.\stop-aigc.bat
```

For a manual backend start:

python -m uvicorn api_server:app --host 127.0.0.1 --port 49152
```

Open the printed URL. For frontend development, keep the backend running and
execute `cd web; npm run dev` in another terminal; the Vite proxy uses
`AIGC_API_PORT` when set.

## Included workflows

- General image generation with Hermes/OpenAI-compatible endpoints.
- Model outfit swap with ordered model and garment references.
- Clothing image-to-image with fabric and construction detail controls.
- Shapewear product images and try-on/video workflows.
- TikTok clothing main images with product-master and detail references.
- Mono-color poster generation.
- Veo and Seedance text-to-video and image-to-video.
- Local image/video import, intelligent editing, batch generation, and
  Cloudflare R2 publishing.

Provider availability depends on the local API URL and key configuration.

## Python entry points

The small command-line interface accepts one JSON request:

```powershell
python main.py
```

Example image request:

```json
{"product":"black seamless shapewear","scene":"premium studio","style":"commercial fashion","type":"image"}
```

Generated files are written to `outputs/images` or `outputs/videos`. Richer
reference-image workflows are documented under `skills/`, `workflows/`, and
`docs/`.

## Cloudflare R2 publishing (optional)

The `uploader` package publishes a local image or video and returns a public
HTTPS URL for providers that require remote references. Configure
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`,
and `R2_PUBLIC_BASE_URL` in `.env`, then run:

```powershell
python uploader/upload.py input/product.jpg
python uploader/upload.py --dir outputs/images
```

Use a bucket-scoped R2 S3 token and a public custom domain. Do not use a
Cloudflare Global API Key.

## Test and build

Tests use fake clients and do not call paid external providers:

```powershell
python -m unittest discover -s tests -p "test_*.py"
cd web
npm run build
```

Provider smoke tests that make real network calls are intentionally separate;
review their source and configure credentials before enabling them.

## Repository layout

```text
api_server.py             FastAPI application and job persistence
main.py                   Python CLI entry points
providers/                Image, video, planner, and safety adapters
skills/                   Reusable workflow prompts and quality checks
harness/                  Anti-loop and supervisor logic
uploader/                 Cloudflare R2 upload client and CLI
web/                      React/Vite studio UI
tests/                    Unit and contract tests
docs/                     Product and architecture notes
```

Generated media, local inputs, `.env`, runtime logs, and dependency caches are
ignored by Git. Keep production/customer assets outside the source repository
or publish them through a controlled object store.

## Security and responsible use

- Keep provider keys in `.env` or a secret manager. Never put them in prompts,
  screenshots, commits, or issue reports.
- Run the studio on loopback unless authentication and a secure reverse proxy
  have been added.
- Review generated media for product accuracy, model consent, rights, and
  platform-policy compliance before publication.

## License

MIT. See `LICENSE`.
