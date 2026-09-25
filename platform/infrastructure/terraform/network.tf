# Single consolidated AWS account (documented simplification vs doc 06 §2's
# 7+ account topology — see SIMPLIFICATIONS.md). Still separates public
# ingress, private application, and an isolated "OT DMZ" subnet tier so the
# OT gateway trust boundary from doc 05 §10 is real at the network layer,
# not just in application code.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-${local.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "app" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + var.az_count)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${var.project_name}-app-${local.azs[count.index]}", Tier = "private-app" }
}

resource "aws_subnet" "data" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 2 * var.az_count)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${var.project_name}-data-${local.azs[count.index]}", Tier = "private-data" }
}

# Isolated tier for the OT gateway simulator — no route to the public
# internet, only reachable from the app tier and (in a real deployment) an
# outbound-initiated mTLS tunnel to a client's site DMZ (doc 06 §3: "inbound
# OT from cloud prohibited by default").
resource "aws_subnet" "ot_dmz" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 3 * var.az_count)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${var.project_name}-ot-dmz-${local.azs[count.index]}", Tier = "ot-dmz" }
}

resource "aws_eip" "nat" {
  count  = var.az_count
  domain = "vpc"
  tags   = { Name = "${var.project_name}-nat-eip-${count.index}" }
}

resource "aws_nat_gateway" "main" {
  count         = var.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${var.project_name}-nat-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "app" {
  count  = var.az_count
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
  tags = { Name = "${var.project_name}-app-rt-${count.index}" }
}

resource "aws_route_table_association" "app" {
  count          = var.az_count
  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.app[count.index].id
}

# Data and OT-DMZ tiers get NO default route to the internet — private,
# reached only via VPC endpoints / peering, matching doc 06 §3's
# no-broad-internet-from-OT requirement.
resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-isolated-rt" }
}

resource "aws_route_table_association" "data" {
  count          = var.az_count
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.isolated.id
}

resource "aws_route_table_association" "ot_dmz" {
  count          = var.az_count
  subnet_id      = aws_subnet.ot_dmz[count.index].id
  route_table_id = aws_route_table.isolated.id
}

resource "aws_security_group" "alb" {
  name_prefix = "${var.project_name}-alb-"
  vpc_id      = aws_vpc.main.id
  ingress {
    # Always open: the getting-started path (var.certificate_arn unset) runs
    # HTTP-only on :80 — see ecs.tf's listener. Once a real domain + ACM
    # cert is added, :80 becomes an HTTP->HTTPS redirect instead of serving
    # traffic directly.
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name_prefix = "${var.project_name}-app-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  ingress {
    description = "service-to-service within the app tier"
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    self        = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "data" {
  name_prefix = "${var.project_name}-data-"
  vpc_id      = aws_vpc.main.id
  ingress {
    description     = "app tier only - no public/OT ingress to the data tier"
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ot_dmz" {
  name_prefix = "${var.project_name}-ot-dmz-"
  vpc_id      = aws_vpc.main.id
  description = "OT command gateway - reachable only from the app tier (BFF calls it), no direct internet or data-tier access"
  ingress {
    from_port       = 8010
    to_port         = 8010
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "outbound-initiated mTLS to a client site DMZ only - narrow this to the clients actual endpoint CIDR before any real deployment"
  }
}
