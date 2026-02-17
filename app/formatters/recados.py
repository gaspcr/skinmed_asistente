from typing import List, Dict


class RecadosFormatter:
    """Formatea recados de FileMaker para WhatsApp."""

    @staticmethod
    def format(data: List[Dict], doctor_name: str) -> str:
        if not data:
            return f"*{doctor_name}*, no tienes recados pendientes. ✅"

        recados = []
        for record in data:
            texto_raw = record.get("fieldData", {}).get("texto_Recado", "")
            parsed = RecadosFormatter._parse_texto_recado(texto_raw)
            if parsed:
                recados.append(parsed)

        if not recados:
            return f"*{doctor_name}*, no tienes recados pendientes. ✅"

        msg = f"*📋 Recados para {doctor_name}*\n"
        msg += f"_{len(recados)} recado(s) encontrado(s)_\n"
        msg += "━━━━━━━━━━━━━━━\n"

        for i, recado in enumerate(recados, 1):
            msg += f"\n*Recado #{i}*\n"
            # Mostrar solo las ultimas 3 entradas del hilo
            entradas = recado[-3:] if len(recado) > 3 else recado
            if len(recado) > 3:
                msg += f"_... {len(recado) - 3} mensaje(s) anterior(es)_\n"

            for entrada in entradas:
                autor = entrada["autor"]
                fecha = entrada["fecha"]
                hora = entrada["hora"]
                mensaje = entrada["mensaje"]
                msg += f"👤 *{autor}* — {fecha} {hora}\n"
                msg += f"   {mensaje}\n"

            msg += "━━━━━━━━━━━━━━━\n"

        return msg.rstrip("\n")

    @staticmethod
    def _parse_texto_recado(texto: str) -> list:
        """
        Parsea el campo texto_Recado de FileMaker.

        Formato:
            autor > dd-mm-yyyy > HH:MM:SS\\r  mensaje\\r---\\rautor > ...
        
        Entradas separadas por \\r---\\r
        Cada entrada: autor > fecha > hora\\r  contenido
        """
        if not texto or not texto.strip():
            return []

        # Normalizar saltos de linea
        texto = texto.replace("\r\n", "\r").replace("\n", "\r")

        # Separar entradas por ---
        bloques = texto.split("---")

        entradas = []
        for bloque in bloques:
            bloque = bloque.strip().strip("\r")
            if not bloque:
                continue

            # Separar la primera linea (header) del contenido
            partes = bloque.split("\r", 1)
            header = partes[0].strip()
            contenido = partes[1].strip() if len(partes) > 1 else ""

            # Parsear header: "autor > fecha > hora"
            header_parts = header.split(">")
            if len(header_parts) >= 3:
                autor = header_parts[0].strip()
                fecha = header_parts[1].strip()
                hora = header_parts[2].strip()
                # Formatear hora a HH:MM
                hora = ":".join(hora.split(":")[:2])
            elif len(header_parts) == 2:
                autor = header_parts[0].strip()
                fecha = header_parts[1].strip()
                hora = ""
            else:
                autor = header
                fecha = ""
                hora = ""

            # Limpiar contenido
            mensaje = contenido.strip()
            if not mensaje:
                mensaje = "(sin contenido)"

            entradas.append({
                "autor": autor,
                "fecha": fecha,
                "hora": hora,
                "mensaje": mensaje,
            })

        return entradas
