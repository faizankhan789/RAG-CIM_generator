from core.config import settings
from core.worker_pool import WorkerPool

# One pool per task type — each has its own queue and concurrency budget
# so slow tasks in one category never starve another.
download_pool = WorkerPool(concurrency=settings.cim_max_concurrent_downloads, name="download")
process_pool = WorkerPool(concurrency=settings.cim_max_concurrent_processing, name="process")

# Registered pools are started and stopped together in main.py lifespan.
# Add a new pool here and it will be lifecycle-managed automatically.
all_pools = [
    download_pool,
    process_pool,
]
