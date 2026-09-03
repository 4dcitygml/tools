<?xml version="1.0" encoding="UTF-8"?>
<!--
  Copyright (c) 2026 4dcitygml
  SPDX-License-Identifier: Apache-2.0

  Encode URO 3.x wrappers as flat CityGML 2.0 genericAttributeSet values for
  3DCityDB import. The inverse is implemented by generic_to_uro.py.
-->
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:uro="https://www.geospatial.jp/iur/uro/3.2"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
  xmlns:gen="http://www.opengis.net/citygml/generics/2.0">

  <xsl:output method="xml" encoding="UTF-8" indent="no"/>

  <xsl:template match="@*|node()">
    <xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>
  </xsl:template>

  <xsl:template name="rel-path">
    <xsl:param name="node"/>
    <xsl:param name="wrapperGi"/>
    <xsl:variable name="parent" select="$node/.."/>
    <xsl:choose>
      <xsl:when test="generate-id($parent) = $wrapperGi">
        <xsl:value-of select="local-name($node)"/>
      </xsl:when>
      <xsl:otherwise>
        <xsl:call-template name="rel-path">
          <xsl:with-param name="node" select="$parent"/>
          <xsl:with-param name="wrapperGi" select="$wrapperGi"/>
        </xsl:call-template>
        <xsl:text>/</xsl:text>
        <xsl:value-of select="local-name($node)"/>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <xsl:template match="*[parent::bldg:Building and contains(namespace-uri(), '/iur/uro/')]">
    <xsl:variable name="wgi" select="generate-id()"/>
    <xsl:variable name="idx"
      select="count(preceding-sibling::*[contains(namespace-uri(), '/iur/uro/') and local-name()=local-name(current())])"/>
    <gen:genericAttributeSet name="uro:{local-name()}#{$idx}">
      <xsl:for-each select=".//*[string-length(normalize-space(text())) &gt; 0]">
        <xsl:variable name="path">
          <xsl:call-template name="rel-path">
            <xsl:with-param name="node" select="."/>
            <xsl:with-param name="wrapperGi" select="$wgi"/>
          </xsl:call-template>
        </xsl:variable>
        <xsl:variable name="txt" select="normalize-space(text())"/>
        <xsl:choose>
          <xsl:when test="@uom">
            <gen:stringAttribute name="{$path}">
              <gen:value><xsl:value-of select="$txt"/></gen:value>
            </gen:stringAttribute>
            <gen:stringAttribute name="{$path}@uom">
              <gen:value><xsl:value-of select="@uom"/></gen:value>
            </gen:stringAttribute>
          </xsl:when>
          <xsl:when test="@codeSpace">
            <gen:stringAttribute name="{$path}">
              <gen:value><xsl:value-of select="$txt"/></gen:value>
            </gen:stringAttribute>
            <gen:stringAttribute name="{$path}@codeSpace">
              <gen:value><xsl:value-of select="@codeSpace"/></gen:value>
            </gen:stringAttribute>
          </xsl:when>
          <xsl:otherwise>
            <gen:stringAttribute name="{$path}">
              <gen:value><xsl:value-of select="$txt"/></gen:value>
            </gen:stringAttribute>
          </xsl:otherwise>
        </xsl:choose>
      </xsl:for-each>
    </gen:genericAttributeSet>
  </xsl:template>
</xsl:stylesheet>
