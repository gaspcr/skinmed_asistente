from typing import List, Dict, Optional


class RecadosFormatter:
    """Formatea recados de FileMaker para WhatsApp."""

    @staticmethod
    def format(data: List[Dict], doctor_name: str, pacient_names: Optional[Dict[str, str]] = None) -> str:
        if not data:
            return f"*{doctor_name}*, no tienes recados pendientes. ✅"

        pacient_names = pacient_names or {}

        recados = []
        for record in data:
            field_data = record.get("fieldData", {})
            texto_raw = field_data.get("texto_Recado", "")
            pac_id = field_data.get("_FK_IDPaciente", "")
            pac_name = pacient_names.get(pac_id, "Paciente desconocido")
            parsed = RecadosFormatter._parse_texto_recado(texto_raw)
            if parsed:
                recados.append({"entradas": parsed, "paciente": pac_name})

        if not recados:
            return f"*{doctor_name}*, no tienes recados pendientes. ✅"

        msg = f"*📋 Recados para {doctor_name}*\n"
        msg += f"_{len(recados)} recado(s) encontrado(s)_\n"
        msg += "━━━━━━━━━━━━━━━\n"

        for i, recado in enumerate(recados, 1):
            msg += f"\n*Recado #{i}* — 🧑‍⚕️ {recado['paciente']}\n"
            # Mostrar solo las ultimas 3 entradas del hilo
            entradas = recado["entradas"]
            visibles = entradas[-3:] if len(entradas) > 3 else entradas
            if len(entradas) > 3:
                msg += f"_... {len(entradas) - 3} mensaje(s) anterior(es)_\n"

            for entrada in visibles:
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
