#!/bin/bash
# Reinicia el receptor FSK para cargar la versión nueva
systemctl restart radioatlas-receptor.service
sleep 3
systemctl is-active radioatlas-receptor.service
echo LISTO
