def main() -> None:
    """`troubadour` / `troubadour serve`: run the engine + API + web UI on TROUBADOUR_HOST:TROUBADOUR_PORT."""
    import logging
    import warnings

    import uvicorn

    from . import config

    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    for noisy in ("httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(f"Troubadour on http://{config.HOST}:{config.PORT}  (LLM {config.LLM_MODEL} @ {config.LLM_URL})")
    uvicorn.run("troubadour.server:app", host=config.HOST, port=config.PORT, log_level="warning")
