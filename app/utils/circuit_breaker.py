"""
Circuit breaker para proteger servicios externos.
Evita llamadas repetidas a servicios caidos, permitiendo recuperacion gradual.

Estados:
    CERRADO  -> Funcionando normal. Tras N fallos consecutivos, pasa a ABIERTO.
    ABIERTO  -> Rechaza llamadas inmediatamente. Tras un timeout, pasa a SEMI-ABIERTO.
    SEMI_ABIERTO -> Permite una llamada de prueba. Si tiene exito, pasa a CERRADO. Si falla, vuelve a ABIERTO.
"""
import asyncio
import logging
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class EstadoCircuito(str, Enum):
    CERRADO = "cerrado"
    ABIERTO = "abierto"
    SEMI_ABIERTO = "semi_abierto"


class CircuitBreakerAbierto(Exception):
    """Excepcion lanzada cuando el circuit breaker esta abierto."""
    def __init__(self, nombre: str, tiempo_restante: float):
        self.nombre = nombre
        self.tiempo_restante = tiempo_restante
        super().__init__(
            f"Circuit breaker '{nombre}' abierto. "
            f"Reintenta en {tiempo_restante:.0f}s"
        )


class CircuitBreaker:
    """
    Circuit breaker asincrono para proteger llamadas a servicios externos.

    Uso:
        cb = CircuitBreaker("filemaker", umbral_fallos=5, timeout_recuperacion=30)

        async def hacer_llamada():
            async with cb:
                return await servicio_externo()
    """

    def __init__(
        self,
        nombre: str,
        umbral_fallos: int = 5,
        timeout_recuperacion: float = 30.0,
        excepciones_monitoreadas: Optional[tuple] = None,
    ):
        self.nombre = nombre
        self.umbral_fallos = umbral_fallos
        self.timeout_recuperacion = timeout_recuperacion
        self.excepciones_monitoreadas = excepciones_monitoreadas or (Exception,)

        self._estado = EstadoCircuito.CERRADO
        self._fallos_consecutivos = 0
        self._ultimo_fallo: float = 0
        self._lock = asyncio.Lock()

    @property
    def estado(self) -> EstadoCircuito:
        if self._estado == EstadoCircuito.ABIERTO:
            if time.monotonic() - self._ultimo_fallo >= self.timeout_recuperacion:
                return EstadoCircuito.SEMI_ABIERTO
        return self._estado

    async def __aenter__(self):
        estado_actual = self.estado

        if estado_actual == EstadoCircuito.ABIERTO:
            tiempo_restante = self.timeout_recuperacion - (time.monotonic() - self._ultimo_fallo)
            raise CircuitBreakerAbierto(self.nombre, max(0, tiempo_restante))

        if estado_actual == EstadoCircuito.SEMI_ABIERTO:
            logger.info("Circuit breaker '%s' semi-abierto, intentando llamada de prueba", self.nombre)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self._registrar_exito()
            return False

        if isinstance(exc_val, self.excepciones_monitoreadas):
            await self._registrar_fallo()

        return False  # No suprimimos la excepcion

    async def _registrar_exito(self):
        async with self._lock:
            if self._estado in (EstadoCircuito.SEMI_ABIERTO, EstadoCircuito.ABIERTO):
                logger.info("Circuit breaker '%s' recuperado -> CERRADO", self.nombre)
            self._estado = EstadoCircuito.CERRADO
            self._fallos_consecutivos = 0

    async def _registrar_fallo(self):
        async with self._lock:
            self._fallos_consecutivos += 1
            self._ultimo_fallo = time.monotonic()

            if self._estado == EstadoCircuito.SEMI_ABIERTO:
                self._estado = EstadoCircuito.ABIERTO
                logger.warning(
                    "Circuit breaker '%s' fallo en semi-abierto -> ABIERTO",
                    self.nombre,
                )
            elif self._fallos_consecutivos >= self.umbral_fallos:
                self._estado = EstadoCircuito.ABIERTO
                logger.warning(
                    "Circuit breaker '%s' abierto tras %d fallos consecutivos",
                    self.nombre,
                    self._fallos_consecutivos,
                )

    def get_info(self) -> dict:
        """Retorna info del estado actual del circuit breaker."""
        return {
            "nombre": self.nombre,
            "estado": self.estado.value,
            "fallos_consecutivos": self._fallos_consecutivos,
            "umbral_fallos": self.umbral_fallos,
            "timeout_recuperacion": self.timeout_recuperacion,
        }
