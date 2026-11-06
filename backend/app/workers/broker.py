import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware import CurrentMessage, Retries
from app.config import settings

# Initialize broker with all standard default middlewares preserved
rabbitmq_broker = RabbitmqBroker(url=settings.RABBITMQ_URL)

# Ensure CurrentMessage middleware is included for actor retry inspection
if not any(isinstance(m, CurrentMessage) for m in rabbitmq_broker.middleware):
    rabbitmq_broker.add_middleware(CurrentMessage())

# Replace the default Retries middleware with our configured retry policy (no duplicates)
for i, m in enumerate(list(rabbitmq_broker.middleware)):
    if isinstance(m, Retries):
        rabbitmq_broker.middleware[i] = Retries(
            max_retries=3,
            min_backoff=15000,   # 15s
            max_backoff=300000,  # 5m
        )
        break
else:
    rabbitmq_broker.add_middleware(
        Retries(
            max_retries=3,
            min_backoff=15000,
            max_backoff=300000,
        )
    )

# Declare durable queue for documents
rabbitmq_broker.declare_queue("documents")

# Set as default Dramatiq broker
dramatiq.set_broker(rabbitmq_broker)
