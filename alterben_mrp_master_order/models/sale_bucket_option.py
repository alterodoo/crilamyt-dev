from odoo import fields, models


class AlterbenBucketOption(models.Model):
    _name = "alterben.bucket.option"
    _description = "Opciones de Bodega por linea"
    _order = "sequence, id"

    name = fields.Char(required=True)
    code = fields.Selection(
        [("CAF", "CAF"), ("CAU", "CAU"), ("CES", "CES"), ("OTROS", "OTROS"), ("AAA", "AAA")],
        required=True,
    )
    token = fields.Char(index=True, required=True)
    sequence = fields.Integer(default=10)
