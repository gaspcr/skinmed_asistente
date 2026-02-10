"""
Excepciones personalizadas para errores de servicios externos.
Permiten distinguir entre 'sin datos' y 'error de infraestructura'.
"""


class ServicioNoDisponibleError(Exception):
    """El servicio externo (FileMaker, WhatsApp) no responde o retorna error."""
    def __init__(self, servicio: str, detalle: str = ""):
        self.servicio = servicio
        self.detalle = detalle
        super().__init__(f"{servicio} no disponible: {detalle}")


class FileMakerAuthError(Exception):
    """Error de autenticacion con FileMaker (token expirado o credenciales invalidas)."""
    pass
