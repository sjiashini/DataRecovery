"""Pydantic request models — every field the SPA sends is validated here."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RecoverAllRequest(BaseModel):
    device: str = ''
    image: str = ''
    output: str = ''
    recup_dir: str = ''
    mode: Literal['normal', 'aggressive'] = 'normal'
    carve: bool = True
    skip_imaging: bool = False
    sector_size: Optional[int] = Field(default=None, ge=512, le=4096)
    file_types: Optional[list[str]] = None
    run_testdisk: bool = True


class ImageRequest(BaseModel):
    image: str = ''
    output: str = ''


class RepairRequest(BaseModel):
    image: str = ''
    output: str = ''
    carve_base: Optional[str] = None
    reference: Optional[str] = None


class CutRequest(BaseModel):
    input: str = ''
    output: str = ''
    start: str = '00:00:00'
    end: str = '00:01:00'
    mode: Literal['copy', 'nvenc'] = 'copy'


class JoinRequest(BaseModel):
    files: list[str] = []
    output: str = ''
    mode: Optional[str] = None


class TranscodeRequest(BaseModel):
    input: str = ''
    output: str = ''
    codec: Optional[str] = None
    quality: Optional[int] = Field(default=None, ge=0, le=51)
    preset: str = 'slow'


class TestDiskRequest(BaseModel):
    image: str = ''


class OpenFolderRequest(BaseModel):
    path: str = ''


class UsbipdBindRequest(BaseModel):
    busid: str = ''


class BrowseSaveRequest(BaseModel):
    initial_name: str = 'Trimmed_output.mp4'
