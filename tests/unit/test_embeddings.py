"""
嵌入模型单元测试
"""
import pytest
from backend.embeddings.embedding_service import EmbeddingService
from backend.embeddings.models import EmbeddingConfig, EmbeddingType


class TestEmbeddings:
    """嵌入模型测试类"""

    def test_local_embedding(self):
        """测试本地嵌入模型"""
        config = EmbeddingConfig(
            embedding_type=EmbeddingType.LOCAL,
            model_name="BAAI/bge-small-zh-v1.5",
            dimensions=512
        )
        service = EmbeddingService(config)
        service.initialize()

        texts = ["这是一个测试句子。", "这是另一个测试句子。"]
        result = service.embed(texts)

        assert len(result.embeddings) == 2
        assert len(result.embeddings[0]) == config.dimensions