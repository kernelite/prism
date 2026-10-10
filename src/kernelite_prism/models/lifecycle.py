from typing import Optional
from pydantic import BaseModel

class DriverLifecycle(BaseModel):
    probe_function: Optional[str] = None
    remove_function: Optional[str] = None
    suspend_function: Optional[str] = None
    resume_function: Optional[str] = None
    init_function: Optional[str] = None
    exit_function: Optional[str] = None