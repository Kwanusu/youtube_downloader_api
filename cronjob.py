"""Run a single Zoho -> YouTube cycle and exit (manual runs / cron fallback).
The long-running service is server.py."""
import pipeline
import store

if __name__ == "__main__":
    print("Results:", pipeline.run_cycle(store.connect()))
