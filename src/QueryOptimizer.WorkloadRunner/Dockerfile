FROM mcr.microsoft.com/dotnet/sdk:10.0.300 AS build

WORKDIR /src
COPY global.json Directory.Build.props Directory.Packages.props QueryOptimizer.sln ./
COPY src/QueryOptimizer.WorkloadRunner/QueryOptimizer.WorkloadRunner.csproj src/QueryOptimizer.WorkloadRunner/
RUN dotnet restore src/QueryOptimizer.WorkloadRunner/QueryOptimizer.WorkloadRunner.csproj

COPY src/QueryOptimizer.WorkloadRunner/ src/QueryOptimizer.WorkloadRunner/
RUN dotnet publish src/QueryOptimizer.WorkloadRunner/QueryOptimizer.WorkloadRunner.csproj \
    --configuration Release \
    --output /app/publish \
    --no-restore

FROM mcr.microsoft.com/dotnet/runtime:10.0.7

WORKDIR /app
COPY --from=build /app/publish .

ENTRYPOINT ["dotnet", "QueryOptimizer.WorkloadRunner.dll"]
