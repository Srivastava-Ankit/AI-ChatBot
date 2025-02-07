import os

from redis.cluster import RedisCluster
from redis.cluster import ClusterNode
# from redis.client import Redis
from redis.cluster import RedisCluster as Redis
from redis.retry import Retry
from redis.backoff import ConstantBackoff

# Configure your Redis Cluster nodes
# REDIS_HOST = os.getenv("REDIS_HOST")
# REDIS_PORT = os.getenv("REDIS_PORT")
# REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
# # Connect to Redis Cluster
# print(f"{REDIS_HOST}, {REDIS_PORT}")
# REDIS_CLIENT = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, password=REDIS_PASSWORD)

def get_redis_client():
    """Factory method to create a Redis client."""
    REDIS_HOST = os.getenv("REDIS_HOST")
    REDIS_PORT = os.getenv("REDIS_PORT")
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

    retry_strategy = Retry(
            backoff=ConstantBackoff(backoff=2), 
            retries=3
        )
    
    """Factory method to create a Redis client."""
    return Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        retry_on_timeout=True,
        retry=retry_strategy,
        decode_responses=True
    )
# startup_nodes = [ClusterNode('127.0.0.1', 6380),
#                         ClusterNode('127.0.0.1', 6381),
#                         ClusterNode('127.0.0.1', 6382)]
# try:
#     REDIS_CLIENT = RedisCluster(startup_nodes=startup_nodes, decode_responses=True)
#     print(f"Redis nodes are - {REDIS_CLIENT.get_nodes()}")
# except Exception as e:
#     print(f"Failed to initialize RedisCluster: {e}")
#     REDIS_CLIENT = None