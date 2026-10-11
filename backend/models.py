"""Única entidad del taller y validación de datos de entrada."""

from enum import Enum
import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator


class Sede(str, Enum):
    tunja = "tunja"
    duitama = "duitama"
    sogamoso = "sogamoso"
    aguazul = "aguazul"


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]


class EstudianteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sede: Sede
    nombre: ShortText
    programa: ShortText
    semestre: int = Field(ge=1, le=12)
    correo: EmailStr
    telefono: Annotated[str, StringConstraints(pattern=r"^3[0-9]{9}$")]
    direccion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=200)]

    @field_validator("sede", mode="before")
    @classmethod
    def normalize_sede(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("telefono", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        if isinstance(value, str):
            value = re.sub(r"[\s()-]", "", value)
            if value.startswith("+57"):
                value = value[3:]
        return value

    @field_validator("correo")
    @classmethod
    def normalize_email(cls, value):
        value = str(value).lower()
        if not re.fullmatch(r"[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]{2,}", value):
            raise ValueError("El correo debe usar letras sin tildes, números, puntos, guiones o guiones bajos.")
        return value


class StudentKey(BaseModel):
    sede: Sede
    id: UUID
