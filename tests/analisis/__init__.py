"""Pruebas del análisis con Claude.

Ninguna prueba abre la red ni llama al claude real: el análisis recibe un `runner` inyectable y el
cliente recibe un `run` inyectable. Este archivo lo hace cumplir: si algo intenta resolver un nombre
o conectar un socket, la prueba falla con un error claro en vez de salir a internet.
"""

import socket


def _sin_red(*_args, **_kwargs):
    raise AssertionError("una prueba intentó usar la red")


socket.getaddrinfo = _sin_red
socket.socket.connect = _sin_red
socket.socket.connect_ex = _sin_red
