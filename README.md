# Courses API

Ensure that dependencies are installed

```sh
poetry install
```

## Running the API Server

Uvicorn is used as the Asynchronous Server Gateway Interface to run our FastAPI application

```sh
poetry run uvicorn src.db.server:app --reload
```

## Run example

```sh
poetry run python src/main.py
```

## Run Tests

### Run all tests

```sh
poetry run python -m unittest discover -s tests
```

### Run specific test

```sh
poetry run python -m unittest tests.test_datafetcher_integration
```

We can show the data in tests by enabling the show_data environment variable (for debugging) as shown below:

```sh
    SHOW_DATA=true poetry run python -m unittest tests.test_datafetcher_integration
```
