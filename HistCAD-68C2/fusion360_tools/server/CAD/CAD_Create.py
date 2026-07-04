from .cad_base import CADBase
from .cad_checklist import CADChecklist
from .cad_constraints import CADConstraints
from .cad_operations import CADOperations
from .cad_primitives import CADPrimitives
from .cad_validation import CADValidation


class CAD_Create(
    CADBase, CADPrimitives, CADOperations, CADConstraints, CADValidation, CADChecklist
):
    def __init__(self):
        super().__init__()
