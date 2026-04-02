import time


def my_timer(func):
    """A decorator to measure execution time of a function."""

    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"\n[INFO] Execution time: {end_time - start_time:.2f} seconds")
        return result

    return wrapper
