"""Pruebas de la lectura de fuentes y de la memoria.

Ninguna prueba abre la red: el lector recibe un `fetcher` inyectable y las pruebas del fetcher real
usan respuestas falsas. Este archivo lo hace cumplir: si algo intenta resolver un nombre o conectar
un socket, la prueba falla con un error claro en vez de salir a internet.
"""

import socket


def _sin_red(*_args, **_kwargs):
    raise AssertionError("una prueba intentó usar la red")


socket.getaddrinfo = _sin_red
socket.socket.connect = _sin_red
socket.socket.connect_ex = _sin_red
