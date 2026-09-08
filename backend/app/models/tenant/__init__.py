"""Import every tenant model so `TenantBase.metadata` is complete.

Used by the local-dev seed script (scripts/seed_tenant_db.py) to create the
schema in throwaway Postgres containers. Never used to create/alter a real
brand's DB — those are owned by the Django monoliths' own migrations.
"""

from app.models.tenant.auth import (  # noqa: F401
    DjangoAuthUser,
    InviteToken,
    Merchant,
    MerchantBalance,
    UserConfig,
    WhiteList,
)
from app.models.tenant.base import TenantBase  # noqa: F401
from app.models.tenant.client import Bank, Card, Currency, SellingInfo  # noqa: F401
from app.models.tenant.mediator import (  # noqa: F401
    Company,
    CompanyBalance,
    CompanyMethodStatistics,
    FloatedProcentsCompanies,
    MerchantPaymentMethod,
    PaymentMethod,
    PaymentMethodCascade,
    PaymentMethodCascadeItem,
    PaymentMethodCompany,
    PaymentMethodTemplate,
    TemplateMethodMapping,
)
from app.models.tenant.sandbox import TestCredits  # noqa: F401
from app.models.tenant.statistics import (  # noqa: F401
    ConversionStatisticsMerchantNew,
    ConversionStatisticsNew,
    ConversionStatisticsPartnersNew,
)
from app.models.tenant.transactions import (  # noqa: F401
    AntiFraudBlockedMerchantUsers,
    Appeal,
    Settlements,
    Transaction,
)
