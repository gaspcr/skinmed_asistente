"""
Utilidad de reintentos con backoff exponencial para llamadas HTTP.
"""
import asyncio
import logging
from typing import Callable, Set, Type, Optional

import httpx

logger = logging.getLogger(__name__)


async def con_reintentos(
    operacion: Callable,
    *args,
    max_intentos: int = 3,
    backoff_base: float = 1.0,
    excepciones_reintentables: Optional[Set[Type[Exception]]] = None,
    nombre_operacion: str = "operacion",
    **kwargs,
):
    """
    Ejecuta una operacion asincrona con reintentos y backoff exponencial.

    Args:
        operacion: Funcion asincrona a ejecutar
        max_intentos: Numero maximo de intentos (default: 3)
        backoff_base: Tiempo base en segundos entre reintentos (default: 1.0)
        excepciones_reintentables: Set de excepciones que justifican reintento
        nombre_operacion: Nombre para logging

    Returns:
        Resultado de la operacion

    Raises:
        La ultima excepcion si todos los intentos fallan
    """
    if excepciones_reintentables is None:
        excepciones_reintentables = {httpx.RequestError}

    ultimo_error = None

    for intento in range(1, max_intentos + 1):
        try:
            return await operacion(*args, **kwargs)
        except tuple(excepciones_reintentables) as e:
            ultimo_error = e
            if intento < max_intentos:
                espera = backoff_base * (2 ** (intento - 1))
                logger.warning(
                    "Reintento %d/%d para %s (espera %.1fs): %s",
                    intento, max_intentos, nombre_operacion, espera, e,
                )
                await asyncio.sleep(espera)
            else:
                logger.error(
                    "Agotados %d intentos para %s: %s",
                    max_intentos, nombre_operacion, e,
                )

    raise ultimo_error
