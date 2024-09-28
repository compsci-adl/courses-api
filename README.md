# Courses API

Ensure that dependencies are installed. Documentation can be found [here](https://fastapi.tiangolo.com/)

```sh
poetry install
poetry add "fastapi[standard]"
```

## Start virtual environment

```sh
poetry shell
```

## Running the API Server

Run the FastAPI server

```sh
fastapi run src/server.py
```

## Running the scraper

```sh
poetry run src/scraper
```

## Testing

DB is mocked, with sample json in MockDB class. The mock class can be modified to test different scenarios such as different search queries

```sh
poetry add --dev pytest httpx
cd tests && poetry run pytest
```
