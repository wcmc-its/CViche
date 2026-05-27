<?php
/**
 * Mock IdP authentication sources for SAML integration testing.
 *
 * Test users for CViche SAML flow validation:
 *   testuser:password  -- Standard WCM user with all attributes
 *   adminuser:password -- Admin user in both access and admin groups
 *   outsider:password  -- User NOT in ED access group (denial testing)
 *   nomail:password    -- User missing mail attribute (validation testing)
 *  
 *  Attribute keys here use SimpleSAMLphp's friendly names (mail, displayName,
 *  eduPersonPrincipalName). The mounted saml20-idp-hosted.php override sets
 *  attributes.NameFormat=urn:oasis:names:tc:SAML:2.0:attrname-format:uri and
 *  adds an authproc core:AttributeMap that rewrites these to OIDs at emit
 *  time -- so the wire format matches what WCM's production IdP sends and
 *  pysaml2's default attribute map decodes them correctly.
 */

$config = [

    'admin' => [
        'core:AdminPassword',
    ],

    'example-userpass' => [
        'exampleauth:UserPass',

        // Standard WCM user with all OID attributes
        'testuser:password' => [
            'mail' => ['testuser@med.cornell.edu'],
            'displayName' => ['Test User'],
            'eduPersonPrincipalName' => ['testuser@cornell.edu'],
        ],

        // Admin user in both access and admin groups
        'adminuser:password' => [
            'mail' => ['adminuser@med.cornell.edu'],
            'displayName' => ['Admin User'],
            'eduPersonPrincipalName' => ['adminuser@cornell.edu'],
        ],

        // User NOT in ED access group (for denial testing)
        'outsider:password' => [
            'mail' => ['outsider@example.com'],
            'displayName' => ['Outside User'],
            'eduPersonPrincipalName' => ['outsider@example.com'],
        ],

        // User missing mail attribute (for attribute validation testing)
        'nomail:password' => [
            'displayName' => ['No Mail User'],
            'eduPersonPrincipalName' => ['nomail@cornell.edu'],
        ],
    ],
];
