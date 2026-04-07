from core.config import settings
from core.worker_pool import WorkerPool

# One pool per task type — each has its own queue and concurrency budget
# so slow tasks in one category never starve another.
download_pool = WorkerPool(concurrency=settings.cim_max_concurrent_downloads, name="download")
# chunk_pool = WorkerPool(concurrency=settings.cim_max_concurrent_chunks, name="chunk")
# retrieval_pool = WorkerPool(concurrency=settings.cim_max_concurrent_retrievals, name="retrieval")

# Registered pools are started and stopped together in main.py lifespan.
# Add a new pool here and it will be lifecycle-managed automatically.
all_pools = [
    download_pool,
    # chunk_pool,
    # retrieval_pool,
]
