# stock-data-factory ランタイム image（k8s デプロイ用 / dagster + streamlit 兼用）。
# devcontainer(.devcontainer/dockerfiles/dev.Dockerfile)は VS Code 開発用。本ファイルは本番/k8s 用の
# スリムな実行 image。component(dagster/streamlit)ごとに k8s 側で command を上書きする。
#
# 実行契約（devcontainer と一致）:
#   DATA_DIR     … 生 XBRL データ(NFS 由来) の参照先
#   DAGSTER_HOME … Dagster の状態ディレクトリ(書込可)
#   PYTHONPATH=/app … repo ルート（apps/ や paths.py を解決。pyproject の 5 パッケージは install 済）
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DATA_DIR=/data/raw \
    DAGSTER_HOME=/dagster_home

# 実行時 OS ライブラリ（lxml/psycopg2-binary 等は wheel 同梱だが念のため最小限）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libxml2 libxslt1.1 libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存＋自パッケージの install を独立レイヤにしてキャッシュを効かせる
# （pyproject / 配下パッケージが変わらない限り再 install しない）。
COPY pyproject.toml README.md ./
COPY dag_process/ dag_process/
COPY db_process/ db_process/
COPY file_processor/ file_processor/
COPY model/ model/
COPY synthetic_dag/ synthetic_dag/
RUN pip install .

# 残りのソース（apps/ paths.py scripts/ など。PYTHONPATH=/app で解決）
COPY . .

RUN mkdir -p "$DAGSTER_HOME" "$DATA_DIR"

EXPOSE 3000 8501

# 既定は Streamlit GUI（具体的な entrypoint がある）。k8s では dagster/streamlit で command 上書き。
CMD ["streamlit", "run", "apps/asset_dashboard_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
