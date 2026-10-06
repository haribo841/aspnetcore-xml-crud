# ASP.NET Core XML CRUD

An ASP.NET Core MVC recruitment exercise with authentication scaffolding and simple user-record CRUD flows. Application records are serialized to a local XML file; this is a learning project, not a production-ready identity or personnel-management system.

[Source](https://github.com/haribo841/aspnetcore-xml-crud) | [Setup guide](docs/SETUP.md) | [MIT license](LICENSE) | [Report an issue](https://github.com/haribo841/aspnetcore-xml-crud/issues)

## Preview and example

![The running MVC application showing two fictional XML-backed records](docs/images/user-records.png)

Actual local application capture with fictional records. The application interface is in Polish. The image demonstrates the XML-backed list only, not a verified login or database deployment.

Example workflow: open the record list, choose **Dodaj użytkownika**, enter fictional development data, save, and reopen the record using **Edytuj**. The record is persisted in the local `users.xml` file. See [the isolated preview instructions](docs/SETUP.md#isolated-ui-preview) to reproduce the screenshot without using a real database or real personal data.

## What it demonstrates

- ASP.NET Core MVC controllers, Razor views, and routing.
- ASP.NET Core Identity backed by Entity Framework Core and SQL Server.
- Model validation for user data.
- XML-based read, create, update, and delete operations through UserXmlService.

## Quick start

Requirements: .NET 7 SDK and a local SQL Server instance accessible to the development account.

~~~powershell
dotnet restore
dotnet user-secrets set "ConnectionStrings:DefaultConnection" "Server=localhost;Database=WebApplication1;Trusted_Connection=True;TrustServerCertificate=True"
dotnet ef database update
dotnet run
~~~

The migration command requires the matching Entity Framework CLI to be available. See [the setup guide](docs/SETUP.md) for configuration details and data-handling notes.

## Supported platform

- ASP.NET Core targeting .NET 7.
- SQL Server through Entity Framework Core.
- A modern desktop browser for the MVC interface.

## Important limitations

The Identity configuration requires confirmed accounts, but authorization is not applied consistently across the CRUD routes. The `Home` routes expose XML operations without an authorization attribute. Do not expose this exercise to the internet or use real personal data. The project also targets the unsupported .NET 7 framework and needs a separate modernization and security review before deployment.

It writes application user records to `users.xml` in the working directory once data is created. Protect database connection strings with user secrets or environment variables, and do not commit generated XML data.

The checked-in development configuration should be overridden locally with a dedicated development database; do not use a system database for application data.

## Documentation, license, and support

- [Setup guide](docs/SETUP.md)
- [Previous README archive](docs/archive/README-2026-09-16.md)
- [MIT license](LICENSE). Bundled browser libraries retain their [original licenses](docs/THIRD-PARTY.md).
- Report a reproducible issue without including database strings, accounts, or user data.
