"""
registry/filter.py

Build APIC query-target-filter strings from a plain Python dict.

APIC filter syntax reference:
  Single predicate  : eq(fvBD.name,"servers")
  Multiple (AND)    : and(eq(fvBD.name,"servers"),eq(fvBD.arpFlood,"yes"))
  Wildcard on dn    : wcard(fvBD.dn,"uni/tn-OT")
  Greater-than      : gt(faultInst.severity,"minor")

This module only generates eq() predicates from caller-supplied key/value pairs.
More complex predicates (gt, lt, wcard, ne) can be added as needed.
"""


def build_filter(class_name: str, filters: dict[str, str]) -> str:
    """Build an APIC query-target-filter string from a dict of attribute filters.

    Each key/value pair becomes an eq() predicate.  When multiple predicates
    are present they are wrapped in and().  An empty dict returns an empty
    string, which tells the APIC client to omit the filter parameter entirely.

    Args:
        class_name: ACI class name used to qualify attribute names in the filter
                    expression, e.g. "fvBD" → eq(fvBD.name,"servers").
        filters:    Dict of {attribute_name: value} pairs.
                    Use the property names listed by get_schema() as keys.
                    Example: {"name": "servers", "arpFlood": "yes"}

    Returns:
        APIC filter string ready to pass as the query-target-filter parameter,
        or an empty string when filters is empty.

    Examples:
        >>> build_filter("fvBD", {})
        ''
        >>> build_filter("fvBD", {"name": "servers"})
        'eq(fvBD.name,"servers")'
        >>> build_filter("fvBD", {"name": "servers", "arpFlood": "yes"})
        'and(eq(fvBD.name,"servers"),eq(fvBD.arpFlood,"yes"))'
    """
    if not filters:
        return ""

    parts = [f'eq({class_name}.{attr},"{val}")' for attr, val in filters.items()]

    if len(parts) == 1:
        return parts[0]

    return "and(" + ",".join(parts) + ")"
