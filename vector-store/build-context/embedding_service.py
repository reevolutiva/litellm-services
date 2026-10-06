from typing import List, Optional
from config import settings, EmbeddingConfig
from litellm.types.utils import EmbeddingResponse
import litellm
import logging

class EmbeddingService:
    """Service for generating embeddings using OpenAI SDK pointed at LiteLLM proxy"""
    
    def __init__(self, config: Optional[EmbeddingConfig] = None):
        self.config = config or settings.embedding

    def _routed_model(self) -> str:
        """Return the proxy alias routed through the `openai/` provider prefix.

        The LiteLLM proxy speaks the OpenAI API, so the SDK must route through
        `openai/`. Calling `litellm.aembedding(model="bge-m3", ...)` with the
        bare alias fails with `LLM Provider NOT provided`: the SDK resolves a
        provider from the prefix and a proxy alias carries none. With the
        prefix, the SDK POSTs the bare alias to the proxy's `/v1/embeddings`,
        which is exactly what the proxy expects. An explicit prefix in the
        configured name (e.g. `openai/bge-m3`) is respected as-is.
        """
        model = self.config.model
        return model if "/" in model else f"openai/{model}"

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text using LiteLLM proxy
        
        Args:
            text: Text to embed
            
        Returns:
            List of floats representing the embedding vector
        """
        try:
            response: EmbeddingResponse = await litellm.aembedding(
                model=self._routed_model(),
                input=[text],
                api_base=self.config.base_url,
                api_key=self.config.api_key
            )
            logging.debug(f"Embedding response: {response}")
            
            # Extract embedding from response
            embedding = response.data[0]["embedding"]
            
            # Validate embedding dimensions
            if len(embedding) != self.config.dimensions:
                raise ValueError(
                    f"Expected embedding dimension {self.config.dimensions}, "
                    f"got {len(embedding)}"
                )
            
            return embedding
            
        except Exception as e:
            raise RuntimeError(f"Failed to generate embedding: {str(e)}")
    
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        try:
            # Generate embeddings using LiteLLM
            response = await litellm.aembedding(
                model=self._routed_model(),
                input=texts,
                api_base=self.config.base_url,
                api_key=self.config.api_key
            )
            
            # LiteLLM may return Embedding objects or plain dictionaries
            embeddings = [
                item["embedding"] if isinstance(item, dict) else item.embedding
                for item in response.data
            ]

            # Validate embedding dimensions
            for i, embedding in enumerate(embeddings):
                if len(embedding) != self.config.dimensions:
                    raise ValueError(
                        f"Expected embedding dimension {self.config.dimensions} for text {i}, "
                        f"got {len(embedding)}"
                    )
            
            return embeddings
            
        except Exception as e:
            raise RuntimeError(f"Failed to generate embeddings: {str(e)}")
    
    def update_config(self, new_config: EmbeddingConfig):
        """Update the embedding configuration"""
        self.config = new_config


# Global embedding service instance
embedding_service = EmbeddingService() 