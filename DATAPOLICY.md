# Data Policy

## Overview
This document outlines the data collection, storage, and privacy practices for the Bluesky Oracle fact-checking bot.

## Data Collection

### What Data We Collect
The bot collects **anonymized analytics data** for misinformation research purposes when performing fact-checks:

#### Fact-Check Analytics
- Fact-check results (TRUE/FALSE/MISLEADING/UNVERIFIABLE)
- Content categories (POLITICAL, HEALTH, SCIENCE, etc.)
- Processing timestamps and response times
- Content pattern analysis (emotional tone, linguistic features)

#### Content Analysis (Anonymized)
- Emotional tone classification
- Presence of statistics, quotes, dates
- Use of absolute language or urgency markers
- Appeals to authority or personal anecdotes
- Basic text structure metrics (mentions, hashtags, punctuation)

### What Data We DO NOT Collect
- **Post content or text** - No actual post text is stored
- **User identities** - No usernames, handles, or identifying information
- **Personal information** - No profile data, location, or demographics
- **Post URLs** - No links to original posts

## Data Storage

### Location
- **Platform**: Google Cloud BigQuery
- **Project**: `oraclebot-465917`
- **Dataset**: `dataset`
- **Table**: `fact-checker`
- **Region**: United States (configurable)

### Security
- Data encrypted in transit and at rest
- Access controlled via Google Cloud IAM
- Service account authentication with minimal permissions
- No public access to raw data

## Anonymization Process

### Complete Content Anonymization
1. **No Content Storage**: Post text is analyzed but never stored in the database
2. **No User Data**: Author information is processed for context but not logged
3. **Pattern Analysis Only**: Only abstract content patterns are recorded
4. **Temporal Aggregation**: Timestamps retain research value without precise tracking

### Data Minimization
- Only essential analytics data is collected
- No personally identifiable information (PII)
- Research-focused data structure
- Automatic expiration policies can be implemented

## Data Usage

### Research Purposes
- Understanding misinformation patterns and spread
- Improving fact-checking accuracy and response times
- Analyzing content characteristics of different claim types
- Academic research collaboration (anonymized datasets only)

### Operational Uses
- Bot performance monitoring and optimization
- Response quality improvement
- System reliability metrics

## Data Retention

### Retention Period
- Analytics data retained indefinitely for research purposes
- No personal data to expire (fully anonymized)
- Aggregated reporting may be published in academic contexts

### Data Deletion
- Individual users cannot request deletion (no personal data stored)
- Regular data reviews for compliance and necessity

## User Rights and Control

### Opt-Out
- Users can avoid data collection by not mentioning the bot
- No tracking of non-interaction users
- Bot only processes explicitly requested fact-checks

### Transparency
- This policy is publicly available
- Open-source codebase allows audit of data practices
- Regular updates to reflect any changes

## Compliance

### Applicable Standards
- GDPR compliance through anonymization and data minimization
- Academic research ethics standards
- Platform terms of service (Bluesky)

### Contact
For questions about this data policy or research collaboration:
- muhammad_muhdhar@berkeley.edu

## Policy Updates

This policy may be updated to reflect changes in data practices, legal requirements, or research needs.

**Last Updated**: July 2025
**Version**: 1.0

---

*This bot is designed for transparent, privacy-first fact-checking research. All data collection serves the public interest of understanding and combating misinformation while protecting individual privacy.*