# Courses API
Courses API is a tool to scrape course information from the University of Adelaide website and provide course data to other CS Club Open Source Team projects via an API endpoint.

## Getting Started

To get started, please follow these steps:

1. Install `uv` if not already installed:

    Linux, macOS, Windows (WSL)
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
    Windows (Powershell)
    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

2. Install dependencies:

    ```sh
    uv sync
    ```

### Running the API Server

1. Start the FastAPI server:

    ```sh
    uv run fastapi dev src/server.py
    ```

2. Open [http://localhost:8000/docs](http://localhost:8000/docs) with your browser to see the API documentation and to test the available endpoints.

### Running the scraper

Start the scraper (Note: Scraping all the courses may take over an hour):

```sh
uv run python3 src/scraper.py
```

## Contributing

We welcome contributions to enhance Courses API! If you find any issues, have suggestions, or want to request a feature, please follow our [Contributing Guidelines](https://github.com/compsci-adl/.github/blob/main/CONTRIBUTING.md).

This repository is configured with a pre-commit linting and formatting tool
To perform a commit from local, pre-commit must first be installed:
```
pip3 install pre-commit
```
OR if performing the action in bash:
```
python3 -m pip install pre-commit
```

Then, as recommended by [Official Pre-Commit Documentation](https://pre-commit.com/):
```
pre-commit install
```
This should be run every time you clone a project

## License

This project is licensed under the MIT License.
See [LICENSE](LICENSE) for details.