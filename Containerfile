FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends man-db && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN argparse-manpage \
      --pyfile cli.py \
      --function build_parser \
      --project-name "born-to-be-wild" \
      --prog "cli.py" \
      --author "nunchuckfusion" \
      --description "Subscriber management CLI for Born to be Wild" \
      > /usr/local/share/man/man1/btwild.1 \
    && mandb -q
CMD ["python", "-m", "src.main"]
